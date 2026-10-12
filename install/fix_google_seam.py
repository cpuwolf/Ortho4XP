#!/usr/bin/env python3
"""
fix_google_seam.py — 修掉 Google(GO2) 影像拼接补丁留下的"黄条纹 / 垂直色调跳变"

问题本质
    Google 的卫星影像马赛克由不同拍摄期、不同匀色的补丁块拼成，补丁边界是一条
    近直线（多为南北向经线），两侧亮度+色偏不一致。Ortho4XP 只是把某个 ZL 的
    源图拼成 4096 的 DDS，跳变原样留在 DDS 里，改 Ortho4XP 参数没用。

处理思路
    1. 定位接缝（自动检测，或 --seam-x / --seam-lon 指定）。
    2. 沿纬度分带（--band 行），每带在接缝两侧各取 --samples 宽的窗口，统计每通道
       10/50/90 分位；锚点沿纬度滑动平均，避免逐带抖动。分带的意义：同一纬度上
       两侧地物最接近，匹配残差最小。
    3. 每带生成"增量 LUT"：out(x)=x+delta(x)，delta 由锚点差值线性插值，锚点外
       常数外插（高光/暗部不失控），末尾单调 + 逐点斜率限幅，避免过冲。
    4. 权重以"逐带跟随到的接缝线"为界硬切：偏黄一侧 100% 校正、另一侧 0，
       只在接缝留 ~2 m 抗锯齿。这一条很关键——两侧对称羽化会同时引入
       "黄侧半校正 + 绿侧被过度校正"，反而多出一条可见细带。
       逐带跟踪是必要的：补丁边界会游走几十米，固定一条竖线会在黄侧留下细线。
    5. 参考块从"含这条经线"的块里挑对比度最强的那块（--seam-lon 指定经度，
       不给就跨块投票）。西侧的衰减距离按米算（--full-m / --ramp-m），
       同一补丁跨多个 4096 块时各块结果连续；逐块各修各的会在块边界制造新接缝。
    6. 写回**保持原格式、原扩展名**：jpg → 同名 jpg（默认 quality 95, 4:4:4），
       dds → nvcompress -bc1（与 Ortho4XP 相同编码路径，含 mipmap）。脚本不做任何
       格式转换——你在 Orthophotos 里的源 jpg 改完还是 jpg，交给 Ortho4XP 原来那套
       编码流程即可（而且比改 DDS 少一次 DXT 压缩损失）。

块名 → 经纬度
    文件名 <y>_<x>_GO2<zl>.jpg / .dds：x = ZL19 全局瓦片列号 - 128（y 同理）。
    已用 +27+089 的 cfg zone_list 校验（x=392304 → lon 89.461669921875）。
    jpg 与 dds 同名同网格（Ortho4XP 的 jpeg_file_name_from_attributes 用的是同一组
    til_x_left/til_y_top），所以两种输入都能直接算经纬度。

位置与运行
    本脚本放在 Ortho4XP/install/ 下，独立运行：不需要设置环境变量，nvcompress 会自动
    按"从脚本位置向上找 Ortho4XP 根 -> 从当前目录向上找 -> $NVCOMPRESS -> PATH"的顺序
    解析，也可以用 --nvcompress 覆盖。除了 --in/--dir 给的文件，不读写别的路径。
    依赖只有 pillow + numpy（Ortho4XP 自己就用这两个），所以用跑 Ortho4XP 的同一个
    解释器即可；脚本不做任何联网操作。

用法（在 Ortho4XP 根目录下执行）
    # 只看效果：输出前后对比预览，不动文件（jpg/dds 都能读）
    python3 install/fix_google_seam.py --in 220544_392304_GO219.jpg --strength 0.7 \
        --preview /tmp/p.png

    # 改源 jpg：扩展名/格式不变，同名写回（--out-dir 换目录，不给则原地并留 .bak）
    python3 install/fix_google_seam.py --dir Orthophotos/+27+089/GO2_19 --recursive \
        --seam-lon 89.46398 --strength 0.7 --write --out-dir /tmp/fixed

    # 改 DDS（原地或换目录），编码仍走 Ortho4XP 同一条 nvcompress -bc1
    python3 install/fix_google_seam.py --in 220544_392304_GO219.dds --strength 0.7 --write

    # 只羽化、不改颜色（把硬线摊成宽带渐变）
    python3 install/fix_google_seam.py --in x.jpg --mode identity --ramp-m 400
"""
import argparse
import glob
import math
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))   # realpath: 软链/别名也能定位到真实位置


def _find_ortho_root(start, levels=4):
    """从 start 向上找 Ortho4XP 根目录（判据：有 Utils/）。找不到返回 None。"""
    d = os.path.abspath(start)
    for _ in range(levels):
        if os.path.isdir(os.path.join(d, "Utils")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


ORTHO_ROOT = _find_ortho_root(HERE) or _find_ortho_root(os.getcwd())

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    sys.exit("缺少依赖：%s\n"
             "用与 Ortho4XP 相同的解释器运行（Ortho4XP 本身就依赖 pillow/numpy），或者：\n"
             "    python3 -m venv ~/.venvs/ortho-seam && ~/.venvs/ortho-seam/bin/pip install pillow numpy\n"
             "    ~/.venvs/ortho-seam/bin/python %s --in x.jpg --strength 0.7 --preview /tmp/p.png\n"
             "（macOS 自带 python3 是 Xcode/LibreSSL 版，直接 pip install 常失败，所以先建 venv）"
             % (exc.name, os.path.basename(__file__)))

ZL19_N = 2 ** 19
NAME_OFFSET = 128          # 块名 = ZL19 全局瓦片号 - 128（已在 +27+089 上校验）
IMG_EXT = (".jpg", ".jpeg", ".png", ".dds", ".tif", ".tiff", ".bmp")


def _nv_candidates():
    """本平台 Ortho4XP 自带 nvcompress 的相对路径候选（相对 Ortho4XP 根）。"""
    if sys.platform.startswith("darwin"):
        return [os.path.join("Utils", "mac", "nvcompress")]
    if os.name == "nt":
        return [os.path.join("Utils", "win", "nvcompress", "nvcompress.exe"),
                os.path.join("Utils", "win", "nvcompress.exe")]
    return [os.path.join("Utils", "lin", "nvcompress")]


DEFAULT_NV = None          # 运行时解析（脚本可以放在任何地方独立跑）


def resolve_nvcompress(user_path=None):
    """找 nvcompress：脚本位置向上找 Ortho4XP 根 -> cwd 向上找 -> 环境变量 -> PATH。

    独立运行不需要任何环境变量；--nvcompress 仍然优先。
    """
    cands = []
    if user_path:
        cands.append(user_path)
    if os.environ.get("NVCOMPRESS"):
        cands.append(os.environ["NVCOMPRESS"])
    roots = [ORTHO_ROOT, _find_ortho_root(HERE), _find_ortho_root(os.getcwd())]
    for root in roots:
        if root:
            for rel in _nv_candidates():
                cands.append(os.path.join(root, rel))
    cands += [os.path.join(HERE, "nvcompress"), os.path.join(HERE, "nvcompress.exe")]
    cands += ["nvcompress", "nvcompress.exe"]
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.sep in c or (os.altsep and os.altsep in c):
            if os.path.isfile(c):
                return c
        elif shutil.which(c):
            return shutil.which(c)
    raise SystemExit("找不到 nvcompress（只有输出 dds 才需要它）。\n"
                     "Ortho4XP 自带：Utils/mac/nvcompress、Utils/lin/nvcompress、"
                     "Utils/win/nvcompress/nvcompress.exe；\n"
                     "也可以用 --nvcompress /path/to/nvcompress 指定。")


# ---------------------------------------------------------------- I/O helpers
def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).astype(np.float32)


def write_dds(rgb_u8, out_path, nvcompress=None, fast=False):
    tmp = out_path + ".tmp.png"
    Image.fromarray(rgb_u8).save(tmp)
    cmd = [resolve_nvcompress(nvcompress), "-bc1"] + (["-fast"] if fast else []) + [tmp, out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(tmp)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("nvcompress failed: " + r.stdout + r.stderr)


def save_like(rgb_u8, out_path, jpeg_quality=95, nvcompress=None, fast=False):
    """按输出路径的扩展名保存：**只在源格式里重写内容，不做格式转换**。

    .dds -> nvcompress -bc1（Ortho4XP 同一条编码链，含 mipmap）
    .jpg/.jpeg -> 同名 jpg（默认 quality 95, 4:4:4）
    .png/.tif/.bmp -> 原样存
    """
    ext = os.path.splitext(out_path)[1].lower()
    if ext == ".dds":
        write_dds(rgb_u8, out_path, nvcompress=nvcompress, fast=fast)
        return
    im = Image.fromarray(rgb_u8)
    if ext in (".jpg", ".jpeg"):
        im.save(out_path, quality=int(jpeg_quality), subsampling=0, optimize=True)
    elif ext == ".png":
        im.save(out_path, compress_level=6)
    else:
        im.save(out_path)


# ------------------------------------------------------------ geo / geometry
def name_to_xy(path):
    m = re.match(r"(\d+)_(\d+)_", os.path.basename(path))
    return (int(m.group(1)) + NAME_OFFSET, int(m.group(2)) + NAME_OFFSET) if m else None


def x_to_lon(x_zl19):
    return x_zl19 / ZL19_N * 360.0 - 180.0


def y_to_lat(y_zl19):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2.0 * y_zl19 / ZL19_N))))


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def block_geo(path, size=4096):
    xy = name_to_xy(path)
    if xy is None:
        return None
    y0, x0 = xy
    lat_top, lat_bot = y_to_lat(y0), y_to_lat(y0 + 16)
    return dict(lon_left=x_to_lon(x0), lon_right=x_to_lon(x0 + 16),
                lat_top=lat_top, lat_bot=lat_bot,
                m_per_px=156543.03392 * math.cos(math.radians((lat_top + lat_bot) / 2)) / ZL19_N,
                px_per_deg=size / (x_to_lon(x0 + 16) - x_to_lon(x0)))


def px_to_lon(path, x_px, size=4096):
    g = block_geo(path, size)
    return g["lon_left"] + x_px / g["px_per_deg"]


def lon_to_px(path, lon, size=4096):
    g = block_geo(path, size)
    return (lon - g["lon_left"]) * g["px_per_deg"]


# ------------------------------------------------------------ seam detection
def column_rg(img):
    cm = img.mean(axis=0)
    return cm[:, 0] - cm[:, 1]


def find_seam(img, min_step=6.0, plateau=192, margin=256, win=101):
    """返回 (seam_x, warm_side)；找不到则 (None, None)。"""
    rg = column_rg(img)
    k = np.ones(win) / win
    sm = np.convolve(np.pad(rg, (win // 2, win // 2), mode="edge"), k, "valid")
    best, best_x = 0.0, None
    for x in range(margin, len(rg) - margin):
        l = sm[x - plateau:x - plateau // 2].mean()
        r = sm[x + plateau // 2:x + plateau].mean()
        if abs(r - l) > best:
            best, best_x = abs(r - l), x
    if best_x is None or best < min_step:
        return None, None
    l = sm[best_x - plateau:best_x - plateau // 2].mean()
    r = sm[best_x + plateau // 2:best_x + plateau].mean()
    return best_x, ("left" if l > r else "right")


def step_across(img, x_px, back=24, ahead=96):
    """跨接缝的 (R-G) 台阶：右侧 window 均值 - 左侧 window 均值。"""
    rg = column_rg(img)
    a = rg[max(0, int(x_px) - ahead):max(1, int(x_px) - back)].mean()
    b = rg[min(len(rg) - 1, int(x_px) + back):min(len(rg), int(x_px) + ahead)].mean()
    return float(b - a)


# ------------------------------------------------------------------- LUT math
def quantile_lut(q_warm, q_cool, strength=1.0, slope_min=0.35, slope_max=1.85):
    """out(x) = x + delta(x)；delta 线性插值 + 常数外插，单调 + 斜率限幅。

    q_warm / q_cool: (n_anchors, 3)
    """
    delta = (np.asarray(q_cool) - np.asarray(q_warm)) * strength
    xin = np.arange(256, dtype=np.float64)
    luts = []
    for c in range(3):
        xs = np.asarray(q_warm[:, c], dtype=np.float64).copy()
        for i in range(1, len(xs)):
            if xs[i] <= xs[i - 1]:
                xs[i] = xs[i - 1] + 1.0
        lut = xin + np.interp(xin, xs, delta[:, c])
        base = float(np.clip(lut[0], 0, 255))
        lut = np.clip(lut, base + slope_min * xin, base + slope_max * xin)
        lut = np.maximum.accumulate(lut)
        luts.append(np.clip(lut, 0, 255))
    return np.stack(luts, axis=1).astype(np.float32)          # (256, 3)


def offset_lut(m_warm, m_cool, strength=1.0):
    lut = np.zeros((256, 3), dtype=np.float32)
    for c in range(3):
        lut[:, c] = np.arange(256) + (m_cool[c] - m_warm[c]) * strength
    return np.clip(lut, 0, 255)


def apply_lut_rows(img, luts_by_band, band_rows):
    out = np.empty_like(img)
    for i, lut in enumerate(luts_by_band):
        y0, y1 = i * band_rows, min(img.shape[0], (i + 1) * band_rows)
        if y1 <= y0:
            break
        blk = img[y0:y1]
        idx = np.clip(np.rint(blk), 0, 255).astype(np.int32)
        out[y0:y1] = np.stack([lut[idx[:, :, c], c] for c in range(3)], axis=2)
    return out


# ------------------------------------------------------------- band analysis
def band_anchors(img, seam_x, warm_side, band_rows=256, sample=420, qs=(10, 50, 90)):
    """接缝两侧各取样窗，按带统计分位数。seam_x 可以是标量或"每带一个 x"的数组。"""
    h, w, _ = img.shape
    n = (h + band_rows - 1) // band_rows
    sx = np.asarray(seam_x, dtype=np.float32)
    sx = np.full(n, float(sx)) if sx.ndim == 0 else sx
    q_warm = np.zeros((n, len(qs), 3))
    q_cool = np.zeros((n, len(qs), 3))
    for i in range(n):
        y0, y1 = i * band_rows, min(h, (i + 1) * band_rows)
        s = float(sx[min(i, len(sx) - 1)])
        if warm_side == "left":
            a = img[y0:y1, max(0, int(s - sample)):max(1, int(s - 40))]
            b = img[y0:y1, min(w - 1, int(s + 40)):min(w, int(s + sample))]
        else:
            a = img[y0:y1, min(w, int(s + 40)):min(w, int(s + sample))]
            b = img[y0:y1, max(0, int(s - sample)):max(1, int(s - 40))]
        q_warm[i] = np.percentile(a.reshape(-1, 3), qs, axis=0)
        q_cool[i] = np.percentile(b.reshape(-1, 3), qs, axis=0)
    for arr in (q_warm, q_cool):                    # 沿纬度平滑锚点
        for k in range(arr.shape[1]):
            for c in range(3):
                v = arr[:, k, c]
                arr[:, k, c] = np.convolve(np.pad(v, (1, 1), mode="edge"), [0.25, 0.5, 0.25], "valid")
    return q_warm, q_cool


def make_preview(before, after, seam_x, path, crop=1600, out_w=780):
    h, w = before.shape[0], before.shape[1]
    x0 = int(np.clip(seam_x - crop // 2, 0, max(0, w - crop)))
    y0 = max(0, h // 2 - crop // 2)
    b = Image.fromarray(np.clip(before[y0:y0 + crop, x0:x0 + crop], 0, 255).astype(np.uint8))
    a = Image.fromarray(np.clip(after[y0:y0 + crop, x0:x0 + crop], 0, 255).astype(np.uint8))
    s = out_w / crop
    b = b.resize((out_w, int(crop * s)), Image.LANCZOS)
    a = a.resize((out_w, int(crop * s)), Image.LANCZOS)
    out = Image.new("RGB", (out_w, b.height * 2 + 10), (255, 0, 0))
    out.paste(b, (0, 0))
    out.paste(a, (0, b.height + 10))
    out.save(path)


# ------------------------------------------------------------------- pipeline
def find_seam_candidates(img, min_step=6.0, plateau=192, margin=256, win=101, nms=160):
    """找所有"持续台阶"候选：返回 [(x, step, warm_side), ...]，按 |step| 降序。

    只取"连续性"台阶（左右 plateau 均值差），并用非极大值抑制避免同一台阶重复。
    """
    rg = column_rg(img)
    k = np.ones(win) / win
    sm = np.convolve(np.pad(rg, (win // 2, win // 2), mode="edge"), k, "valid")
    cands = []
    for x in range(margin, len(rg) - margin):
        l = sm[x - plateau:x - plateau // 2].mean()
        r = sm[x + plateau // 2:x + plateau].mean()
        if abs(r - l) >= min_step:
            cands.append((x, float(r - l)))
    cands.sort(key=lambda t: -abs(t[1]))
    kept = []
    for x, step in cands:
        if all(abs(x - kx) > nms for kx, _, _ in kept):
            kept.append((x, step, "left" if step < 0 else "right"))
    return kept


def detect_seam_lon(files, max_files=6, tol_px=200, min_step=6.0):
    """跨文件聚合：真正的补丁接缝在多个块里出现在**同一经度**上。

    返回 (ref_file, seam_x_in_ref, warm_side, lon_seam, 支撑数) 或 (None,)*5
    """
    votes = []                       # (lon, step, side, file, x)
    for f in files[:max_files]:
        try:
            img = load_rgb(f)
        except Exception:
            continue
        for x, step, side in find_seam_candidates(img, min_step=min_step):
            g = block_geo(f, img.shape[1])
            votes.append((px_to_lon(f, x, img.shape[1]), abs(step), side, f, x))
    if not votes:
        return None, None, None, None, 0
    g0 = block_geo(files[0])
    tol_deg = tol_px / g0["px_per_deg"]
    votes.sort()
    best = None
    for i in range(len(votes)):
        lo = votes[i][0]
        grp = []
        for j in range(i, len(votes)):
            if votes[j][0] - lo <= tol_deg:
                grp.append(votes[j])
            else:
                break
        if not grp:
            continue
        files_in = len({t[3] for t in grp})
        score = files_in * sum(t[1] for t in grp) / len(grp)
        if best is None or score > best[0]:
            best = (score, grp)
    grp = best[1]
    ref_lon = float(np.mean([t[0] for t in grp]))
    top = max(grp, key=lambda t: t[1])
    sides = [t[2] for t in grp]
    side = max(set(sides), key=sides.count)
    return top[3], top[4], side, ref_lon, len({t[3] for t in grp})


def track_seam(img, seed_x, search=300, band_rows=256, p_lo=48, p_hi=144, win=31,
               passes=2, max_drift=250):
    """逐带跟随接缝：带内找使"左右 plateau 均值差"最大的 x。

    补丁边界不是数学直线，会游走；用逐带位置做权重分界，才不会留细线/压暗绿侧。
    策略：先粗跟一步 -> 中值滤波 -> 限制漂移 -> 再以平滑轨迹为种子精跟一步，
    避免在接缝对比度低的带里跟到别的地物边缘上。
    """
    h, w, _ = img.shape
    n = (h + band_rows - 1) // band_rows
    seeds = np.full(n, float(seed_x), dtype=np.float32)
    fine = max(48, int(p_hi))
    for p in range(max(1, passes)):
        srch = int(search if p == 0 else max(80, search // 2))
        cur = np.empty(n, dtype=np.float32)
        for i in range(n):
            y0, y1 = i * band_rows, min(h, (i + 1) * band_rows)
            if y1 <= y0:
                cur[i] = seeds[i]
                continue
            blk = img[y0:y1]
            rg = blk.mean(axis=0)[:, 0] - blk.mean(axis=0)[:, 1]
            sm = np.convolve(np.pad(rg, (win // 2, win // 2), mode="edge"),
                             np.ones(win) / win, "valid")
            s = float(seeds[i])
            lo = int(max(fine, s - srch))
            hi = int(min(w - fine, s + srch))
            best, bx = -1.0, s
            for x in range(lo, hi):
                d = abs(sm[x + p_lo:x + p_hi].mean() - sm[x - p_hi:x - p_lo].mean())
                if d > best:
                    best, bx = d, x
            cur[i] = bx
        # 中值滤波 + 限制漂移
        cur = np.array([np.median(cur[max(0, i - 2):i + 3]) for i in range(n)], dtype=np.float32)
        cur = np.clip(cur, seed_x - max_drift, seed_x + max_drift)
        seeds = cur
    return seeds


def pick_reference(files, lon_seam, band, search, p_lo=48, p_hi=144):
    """在"含这条经线"的块里挑接缝对比度最明显的那个块做参考。

    补丁接缝在不同纬度上对比度不同（林地/河谷处可能几乎看不出），
    必须挑最清楚的那块来估计 LUT，否则会把噪声当校正量。
    返回 (file, seed_x, rows, side, score) 或 None。
    """
    best = None
    for f in files:
        g = block_geo(f)
        if g is None or not (g["lon_left"] <= lon_seam <= g["lon_right"]):
            continue
        img = load_rgb(f)
        h, w, _ = img.shape
        seed = lon_to_px(f, lon_seam, w)
        rows = track_seam(img, seed, search=search, band_rows=band)
        steps = []
        for i, x in enumerate(rows):
            y0, y1 = i * band, min(h, (i + 1) * band)
            if y1 <= y0:
                continue
            r = img[y0:y1].mean(axis=0)
            rg = r[:, 0] - r[:, 1]
            x = int(x)
            lo, hi = max(0, x - p_hi), min(w, x + p_hi)
            if hi - lo < 2 * p_lo + 20 or x - p_lo <= lo or x + p_lo >= hi:
                continue
            steps.append(float(rg[x + p_lo:hi].mean() - rg[lo:x - p_lo].mean()))
        if not steps:
            continue
        score = float(np.median(np.abs(steps)))
        sign = float(np.median(steps))
        if best is None or score > best[4]:
            best = (f, seed, rows, ("left" if sign < 0 else "right"), score)
        del img
    return best


def fmt(v):
    return "—" if (v is None or (isinstance(v, float) and not np.isfinite(v))) else "%.1f" % v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="single")
    ap.add_argument("--dir", dest="folder")
    ap.add_argument("--pattern", default="*_GO2*", help="默认同时匹配 jpg/dds")
    ap.add_argument("--recursive", action="store_true", help="在 --dir 下递归查找（Orthophotos 子目录）")
    ap.add_argument("--seam-x", default="auto")
    ap.add_argument("--seam-lon", type=float, default=None)
    ap.add_argument("--strength", type=float, default=0.7)
    ap.add_argument("--mode", choices=["match", "offset", "identity"], default="match")
    ap.add_argument("--band", type=int, default=256, help="分带行数")
    ap.add_argument("--full-m", type=float, default=250.0, help="接缝以西多少米内保持 100%% 校正")
    ap.add_argument("--ramp-m", type=float, default=700.0, help="再往西多少米内把校正量渐变到 0")
    ap.add_argument("--feather-m", type=float, default=8.0, help="接缝处抗锯齿过渡宽度(米)")
    ap.add_argument("--samples", type=int, default=420, help="接缝两侧取样窗宽(px)")
    ap.add_argument("--min-step", type=float, default=6.0, help="判定接缝的最小 R-G 台阶（灰阶）")
    ap.add_argument("--search-px", type=int, default=300, help="逐带跟踪接缝时的搜索半径(px)")
    ap.add_argument("--aa-m", type=float, default=2.0, help="接缝处分界抗锯齿宽度(米)")
    ap.add_argument("--slope-min", type=float, default=0.35)
    ap.add_argument("--slope-max", type=float, default=1.85)
    ap.add_argument("--preview")
    ap.add_argument("--write-dds", "--write", dest="write_dds", action="store_true",
                    help="写回结果（扩展名/格式保持不变：dds->dds，jpg->jpg）")
    ap.add_argument("--out-dir", help="输出目录；文件名与扩展名不变。不给则原地覆盖（自动 .bak 备份）")
    ap.add_argument("--jpeg-quality", type=int, default=95, help="写回 jpg 时的质量（默认 95）")
    ap.add_argument("--nvcompress", default=None,
                    help="nvcompress 路径（仅在处理 .dds 时需要）；默认按平台在 Ortho4XP/Utils 下自动查找")
    ap.add_argument("--fast", action="store_true", help="nvcompress -fast")
    args = ap.parse_args()

    if args.single:
        files = [args.single]
    else:
        root = args.folder or "."
        g = (glob.glob(os.path.join(root, "**", args.pattern), recursive=True) if args.recursive
             else glob.glob(os.path.join(root, args.pattern)))
        files = sorted(f for f in g
                       if os.path.splitext(f)[1].lower() in IMG_EXT and not f.endswith(".bak"))
    if not files:
        sys.exit("no input files")

    if args.write_dds and any(os.path.splitext(f)[1].lower() == ".dds" for f in files):
        print("nvcompress : %s" % resolve_nvcompress(args.nvcompress))
    exts = sorted({os.path.splitext(f)[1].lower() for f in files})
    print("输入       : %d 个文件，格式 %s（输出保持同扩展名）" % (len(files), "/".join(exts)))

    # ---- 1. 接缝：定经度 -> 在含这条线的块里挑对比度最强的那块做参考 ----
    ref = seam_x = None
    warm_side = "left"
    support = 0
    ref_rows = None
    if args.seam_lon is not None:
        lon_seam = args.seam_lon
        if not any(block_geo(f) and block_geo(f)["lon_left"] <= lon_seam <= block_geo(f)["lon_right"]
                   for f in files):
            sys.exit("--seam-lon 不落在任何输入块内")
    elif args.seam_x != "auto" and len(files) == 1:
        img = load_rgb(files[0])
        ref, seam_x = files[0], int(args.seam_x)
        rg = column_rg(img)
        warm_side = "left" if rg[:seam_x].mean() > rg[seam_x:].mean() else "right"
        lon_seam = px_to_lon(ref, seam_x)
    else:
        r0, x0, side0, lon0, support = detect_seam_lon(files, min_step=args.min_step)
        if r0 is None:
            sys.exit("未检测到接缝；用 --seam-x / --seam-lon 手工指定")
        lon_seam = lon0

    if ref is None:
        pick = pick_reference(files, lon_seam, args.band, args.search_px)
        if pick is None:
            sys.exit("没有任何块包含这条经线")
        ref, seam_x, ref_rows, warm_side, score = pick
        print("参考块挑选用对比度 %.1f 灰阶衡量" % score)
        if score < 3.0:
            print("警告：参考块里这条接缝的对比度很低，可能这条经线在这里并不是真正的补丁边界")

    g = block_geo(ref)
    lat_mid = (g["lat_top"] + g["lat_bot"]) / 2
    if args.seam_lon is None:
        lon_seam = px_to_lon(ref, seam_x)
    print("参考块    : %s  %s" % (os.path.basename(ref),
                                ("（%d 个块在同一经度上支撑这条接缝）" % support) if support else ""))
    print("接缝      : x=%d px -> lon=%.7f   (lat≈%.4f, %.3f m/px)"
          % (seam_x, lon_seam, lat_mid, g["m_per_px"]))
    print("偏黄一侧  : %s" % ("西侧" if warm_side == "left" else "东侧"))

    # ---- 2. 参考块的逐带接缝位置 + 分带锚点 ----
    img_ref = load_rgb(ref)
    seed_ref = lon_to_px(ref, lon_seam, img_ref.shape[1])
    if ref_rows is None:
        if -64 <= seed_ref <= img_ref.shape[1] + 64:
            ref_rows = track_seam(img_ref, seed_ref, search=args.search_px, band_rows=args.band)
        else:
            ref_rows = np.full((img_ref.shape[0] + args.band - 1) // args.band, seed_ref, dtype=np.float32)
    spread = float(np.ptp(ref_rows))
    seam_x = int(np.median(ref_rows))          # 报一个代表性位置
    q_warm, q_cool = band_anchors(img_ref, ref_rows, warm_side, args.band, args.samples)
    print("分带      : %d 带 x %d 行，锚点已沿纬度平滑；接缝在块内游走 %.0f px (%.0f m)"
          % (len(q_warm), args.band, spread, spread * g["m_per_px"]))
    print("黄侧 带0 p10/50/90: %s" % np.round(q_warm[0], 0).tolist())
    print("绿侧 带0 p10/50/90: %s" % np.round(q_cool[0], 0).tolist())

    luts = []
    for i in range(len(q_warm)):
        if args.mode == "identity":
            luts.append(None)
        elif args.mode == "offset":
            luts.append(offset_lut(q_warm[i][1], q_cool[i][1], args.strength))
        else:
            luts.append(quantile_lut(q_warm[i], q_cool[i],
                                     args.strength, args.slope_min, args.slope_max))
    if luts[0] is not None:
        lut = luts[len(luts) // 2]
        print("LUT(中带, 强度%.2f) in->out: %s"
              % (args.strength, "  ".join("%d->%s" % (v, np.round(lut[v], 0).tolist())
                                          for v in (32, 96, 160, 224))))

    # ---- 3. 校正权重：以"逐带跟踪到的接缝线"为界（硬切 + 几米抗锯齿），
    #         西侧的衰减距离按米算，保证跨块连续 ----
    full_px_g = args.full_m / g["m_per_px"]
    ramp_px_g = args.ramp_m / g["m_per_px"]
    aa_px = max(2.0, args.aa_m / g["m_per_px"])
    reach_m = args.full_m + args.ramp_m

    done = 0
    for f in files:
        gg = block_geo(f)
        if gg is not None and gg["lon_right"] < lon_seam - reach_m / m_per_deg_lon(lat_mid):
            continue                                   # 整块在补丁之外，不动
        img = load_rgb(f)
        h, w, _ = img.shape
        gg = block_geo(f, w)
        seed = lon_to_px(f, lon_seam, w)
        contains = -64 <= seed <= w + 64

        if contains:
            # 逐带跟踪这条线 -> 插值成逐行位置
            seam_bands = track_seam(img, seed, search=args.search_px, band_rows=args.band)
            centres = (np.arange(len(seam_bands)) + 0.5) * args.band
            seam_rows = np.interp(np.arange(h), centres, seam_bands).astype(np.float32)
            dist_px = seam_rows[:, None] - np.arange(w, dtype=np.float32)[None, :]   # >0 在西(黄)侧
        else:
            # 整块都在这条线以西：按经度算距离（此时距离单调，不需要跟踪）
            lons = gg["lon_left"] + np.arange(w, dtype=np.float32) / gg["px_per_deg"]
            dist_px = np.broadcast_to(((lon_seam - lons) * gg["px_per_deg"])[None, :], (h, w)).copy()

        weight = np.clip(np.where(dist_px > full_px_g,
                                  np.clip((full_px_g + ramp_px_g - dist_px) / ramp_px_g, 0, 1),
                                  1.0), 0, 1)
        weight = np.where(dist_px < 0.0, 0.0, weight)             # 另一侧完全不动
        weight = np.where(np.abs(dist_px) < aa_px,                # 接缝几米抗锯齿
                          np.clip(0.5 + dist_px / (2 * aa_px), 0, 1), weight).astype(np.float32)
        if warm_side == "right":
            weight = 1.0 - weight

        if args.mode == "identity":
            r_px = int(max(8, round(2 * args.ramp_m / gg["m_per_px"])))
            band = np.abs(np.asarray(dist_px, dtype=np.float32)) < ramp_px_g
            out = img
            if band.any():
                box = np.ones(r_px) / r_px
                sm = np.empty_like(img)
                for c in range(3):
                    sm[:, :, c] = np.apply_along_axis(
                        lambda r: np.convolve(np.pad(r, (r_px // 2, r_px // 2), mode="edge"), box, "valid"),
                        1, img[:, :, c])
                out = np.where(band[:, :, None], sm, img)
        else:
            corrected = apply_lut_rows(img, luts, args.band)
            out = img * (1.0 - weight[:, :, None]) + corrected * weight[:, :, None]

        sx_px = int(seed) if contains else w            # 块内没有这条线时，用整块均值做参考
        b_step, a_step = step_across(img, sx_px), step_across(out, sx_px)
        print("[%s] 跨缝 Δ(R-G): %s -> %s   校正权重均值 %.0f%%%s"
              % (os.path.basename(f), fmt(b_step), fmt(a_step), 100 * weight.mean(),
                 "" if contains else "   （此块全在接缝以西）"))

        if args.preview:
            p = args.preview if len(files) == 1 else os.path.join(
                os.path.dirname(args.preview), os.path.basename(f)[:-4] + "_prev.png")
            make_preview(img, out, sx_px, p)
        if args.write_dds:
            out_u8 = np.clip(out, 0, 255).astype(np.uint8)
            if args.out_dir:
                # 同名同扩展名，只是换个目录
                os.makedirs(args.out_dir, exist_ok=True)
                dst = os.path.join(args.out_dir, os.path.basename(f))
            else:
                dst = f
                if not os.path.exists(f + ".bak"):
                    shutil.copy2(f, f + ".bak")
            save_like(out_u8, dst, jpeg_quality=args.jpeg_quality, nvcompress=args.nvcompress, fast=args.fast)
        done += 1
    print("处理 %d 个文件（扩展名/格式不变）%s"
          % (done, "，输出目录 %s" % args.out_dir if args.out_dir else "，原文件已备份为 .bak"))


if __name__ == "__main__":
    main()