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
    文件名 <y>_<x>_<provider><zl>.jpg/.dds 里的 y/x **就是该 zoom 下的瓦片号（无偏移）**，
    一个 4096px 块 = 16 个瓦片。所以 lon_left = x / 2**zl * 360 - 180，
    lat 用 Mercator 反算，分辨率 = 156543.03392 * cos(lat) / 2**zl。
    同一个 textures 目录里常常混着多个 zoom（+27+089 实测有 ZL13/15/16/17/18/19），
    所以必须按文件名里的 zoom 换算，不能写死 ZL19——否则 ZL18 的块会被算成 2 倍宽。
    校验方式：该切片的 cfg zone_list 里一条 zone 矩形恰好等于一个 4096 块，
    183 个文件（ZL15..19）在"偏移 0"下 100% 命中 zone 角点。

位置与运行
    本脚本放在 Ortho4XP/install/ 下，独立运行：不需要设置环境变量，nvcompress 会自动
    按"从脚本位置向上找 Ortho4XP 根 -> 从当前目录向上找 -> $NVCOMPRESS -> PATH"的顺序
    解析，也可以用 --nvcompress 覆盖。除了 --in/--dir 给的文件，不读写别的路径。
    依赖只有 pillow + numpy（Ortho4XP 自己就用这两个），所以用跑 Ortho4XP 的同一个
    解释器即可；脚本不做任何联网操作。

自动扫描（推荐用法）
    给一个目录，它自己干三件事：
      1. 逐块找"贯穿全高、方向一致的竖直台阶"（检测在 1/4 缩图上做，jpg 走缩解码）；
      2. 把各块命中的台阶按**绝对经度**聚组：真正的 Google 补丁边界会横跨多块，
         在同一经线上被多个块同时命中 -> 才算"确认条纹"；
      3. 对确认的组自动估 LUT 并修掉它影响的每个块（含西侧衰减范围内的块，保证
         跨块连续），其余文件不动。
    单块命中的一律只列进"疑似"清单不自动修：实测云影/地物边界单块也能有 40+ 灰阶
    的近垂直台阶，靠单块统计与拼接缝区分不开，只有跨块支撑靠得住。

用法（在 Ortho4XP 根目录下执行）
    # 只扫描列出（不改文件）：哪些块有条纹、在哪一列、多少灰阶、几块支撑
    python3 install/fix_google_seam.py --dir ".../zOrtho4XP_+27+089/textures" --scan

    # 扫描 + 导出可排序 HTML 报告（内嵌预览图：整块缩略图 + 缝附近全分辨率裁切，点图放大）
    python3 install/fix_google_seam.py --dir ".../textures" --scan \
        --export-html scan.html --export-csv scan.csv
    # 预览图太多/不想内嵌时：--no-thumbs / --thumb-max 200 / --thumb-px 512 / --thumb-q 75

    # 扫描 + 自动修（--out-dir 换目录；不给则原地并留 .bak）
    python3 install/fix_google_seam.py --dir ".../Orthophotos/+27+089/GO2_19" --recursive \
        --write --out-dir /tmp/fixed

    # 单文件预览（jpg/dds 都能读，不动文件）
    python3 install/fix_google_seam.py --in 220544_392304_GO219.jpg --strength 0.7 \
        --preview /tmp/p.png

    # 手工指定接缝经度（自动分组挑错时用；从 --scan 的输出里抄经度）
    python3 install/fix_google_seam.py --dir ... --seam-lon 89.3760967 --write

    # 只羽化、不改颜色（把硬线摊成宽带渐变）
    python3 install/fix_google_seam.py --in x.jpg --mode identity --ramp-m 400
"""
import argparse
import base64
import collections
import glob
import html as html_mod
import io
import math
import os
import re
import shutil
import subprocess
import sys
import time

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
    from PIL import Image, ImageDraw
except ImportError as exc:
    sys.exit("缺少依赖：%s\n"
             "用与 Ortho4XP 相同的解释器运行（Ortho4XP 本身就依赖 pillow/numpy），或者：\n"
             "    python3 -m venv ~/.venvs/ortho-seam && ~/.venvs/ortho-seam/bin/pip install pillow numpy\n"
             "    ~/.venvs/ortho-seam/bin/python %s --in x.jpg --strength 0.7 --preview /tmp/p.png\n"
             "（macOS 自带 python3 是 Xcode/LibreSSL 版，直接 pip install 常失败，所以先建 venv）"
             % (exc.name, os.path.basename(__file__)))

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
def load_rgb(path, draft=None):
    """draft：给 jpg 时用 DCT 缩解码（1/4 尺寸直接解出，检测阶段用，快约一个数量级）。"""
    with Image.open(path) as im:
        if draft:
            im.draft("RGB", (int(draft), int(draft)))
        return np.asarray(im.convert("RGB")).astype(np.float32)


def load_rgb_detect(path, target=1024):
    """检测专用的小图；返回 (小图, step)，小图坐标 × step = 原图坐标。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        with Image.open(path) as im:
            w0 = im.size[0]
            im.draft("RGB", (int(target), int(target)))
            arr = np.asarray(im.convert("RGB")).astype(np.float32)
        step = (w0 / float(arr.shape[1])) if arr.shape[1] else 1.0
    else:
        arr = load_rgb(path)
        step = max(1, int(round(arr.shape[1] / float(target))))
        if step > 1:
            arr = arr[::step, ::step]
    return arr, float(step)


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
NAME_RE = re.compile(r"^(\d+)_(\d+)_[A-Za-z0-9]*?(\d{2})$")


def parse_name(path):
    """<y>_<x>_<provider><zl>.{jpg,dds} -> (y, x, zl)；不合规范返回 None。"""
    m = NAME_RE.match(os.path.splitext(os.path.basename(path))[0])
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def x_to_lon(x, zl):
    return x / float(2 ** zl) * 360.0 - 180.0


def y_to_lat(y, zl):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2.0 * y / 2 ** zl))))


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def block_geo(path, size=4096):
    """块的地理范围 + 分辨率。**必须按文件名的 zoom 换算**（同一目录常混 ZL13..19）。

    命名规则 = 该 zoom 下的瓦片号（无偏移）+ 4096px 块 = 16 个瓦片。
    已用 +27+089 的 cfg zone_list 校验：一条 zone 矩形正好等于一个 4096 块，
    183 个文件（ZL15..19）在偏移 0 下 100% 命中 zone 角点，偏移 128 只是偶合。
    """
    p = parse_name(path)
    if p is None:
        return None
    y0, x0, zl = p
    lat_top, lat_bot = y_to_lat(y0, zl), y_to_lat(y0 + 16, zl)
    lon_left, lon_right = x_to_lon(x0, zl), x_to_lon(x0 + 16, zl)
    return dict(zl=zl, lon_left=lon_left, lon_right=lon_right,
                lat_top=lat_top, lat_bot=lat_bot,
                m_per_px=156543.03392 * math.cos(math.radians((lat_top + lat_bot) / 2)) / 2 ** zl,
                px_per_deg=size / (lon_right - lon_left))


def px_to_lon(path, x_px, size=4096):
    g = block_geo(path, size)
    return g["lon_left"] + x_px / g["px_per_deg"]


def lon_to_px(path, lon, size=4096):
    g = block_geo(path, size)
    return (lon - g["lon_left"]) * g["px_per_deg"]


# ------------------------------------------------- 条纹检测（贯穿全高的竖直台阶）
def column_rg(img):
    cm = img.mean(axis=0)
    return cm[:, 0] - cm[:, 1]


def band_step_map(img, band_rows=256, p_lo=48, p_hi=144, sig="rg"):
    """(n_bands, w)：每带、每列的"右侧 plateau 均值 - 左侧 plateau 均值"。O(w)/带。"""
    h, w, _ = img.shape
    n = max(1, (h + band_rows - 1) // band_rows)
    out = np.zeros((n, w), dtype=np.float32)
    x = np.arange(w)
    for i in range(n):
        y0, y1 = i * band_rows, min(h, (i + 1) * band_rows)
        if y1 <= y0:
            continue
        m = img[y0:y1].mean(axis=0)
        s = (m[:, 0] - m[:, 1]) if sig == "rg" else m.mean(axis=1)
        cs = np.concatenate([[0.0], np.cumsum(s, dtype=np.float64)])

        def rmean(off):
            a = np.clip(x + off[0], 0, w)
            b = np.clip(x + off[1], 0, w)
            return (cs[b] - cs[a]) / np.maximum(b - a, 1)

        out[i] = rmean((p_lo, p_hi)) - rmean((-p_hi, -p_lo))
    return out


def detect_stripes(img, band_rows=256, min_step=6.0, p_lo=48, p_hi=144, nms=200,
                   edge=200, cohere=0.5):
    """找"贯穿全高、方向一致"的竖直色调台阶（= 影像拼接缝 / 云影边界 / 地物边）。

    判据不是"某处有台阶"，而是"每一带在同一个 x 上都有同向台阶"：
      纵向取中位数 -> 只在少数带出现的地物边缘（河、田埂）被压掉；
      同向带占比 -> 进一步惩罚左右来回跳的噪声。
    分别在 R-G（色偏）和亮度上跑，谁强用谁。

    **注意**：单块图像上，Google 补丁边界和云影边界几乎无法区分（实测云影台阶
    可达 43 灰阶、同样是近垂直的硬边）。所以这里只负责"找出竖直台阶"，
    "是不是真的拼接缝"由 group_stripes 的跨块支撑（同一经度多块命中）来定。

    返回 [(x, severity, side, sig, rg_step)]，severity = |中位数台阶| × 同向性系数。
    side = 需要被校正的一侧（rg: 偏暖的一侧；y: 偏亮的一侧）。
    """
    h, w, _ = img.shape
    meds = {}
    agrees = {}
    for sig in ("rg", "y"):
        S = band_step_map(img, band_rows, p_lo, p_hi, sig)
        meds[sig] = np.median(S, axis=0)
        agrees[sig] = np.abs(np.mean(np.sign(np.where(np.abs(S) > 3.0, S, 0)), axis=0))
    cands = []
    for sig in ("rg", "y"):
        med, agree = meds[sig], agrees[sig]
        score = np.abs(med) * np.clip((agree - cohere) / (1.0 - cohere), 0, 1)
        for x in range(edge, w - edge):
            s = float(score[x])
            if s < min_step:
                continue
            if score[max(0, x - nms):x + nms].max() > s:      # 非极大值抑制
                continue
            cands.append((x, s, "left" if med[x] < 0 else "right", sig, float(meds["rg"][x])))
    cands.sort(key=lambda t: -t[1])
    kept = []
    for c in cands:
        if all(abs(c[0] - k[0]) > nms for k in kept):        # rg/y 同一位置只留强者
            kept.append(c)
    return kept


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
SCAN_TARGET = 1024         # 检测用小图的目标边长（jpg 缩解码；dds 解码后抽样）


def scan_files(files, band=256, samples=420, min_step=6.0, target=SCAN_TARGET, quiet=False):
    """逐文件检测条纹。返回 [(file, cands)]，cands = [(x_full, sev, side, sig)]。

    检测在 1/4 小图上做（窗口/分带同步缩放），所以扫几百个块也还能忍。
    """
    res = []
    for f in files:
        try:
            small, step = load_rgb_detect(f, target)
        except Exception as exc:
            if not quiet:
                print("  ! %s 读取失败：%s" % (os.path.basename(f), exc))
            res.append((f, []))
            continue
        scale = 1.0 / step
        cands = detect_stripes(small,
                               band_rows=max(16, int(round(band * scale))),
                               min_step=min_step,
                               p_lo=max(6, int(round(48 * scale))),
                               p_hi=max(12, int(round(144 * scale))),
                               nms=max(24, int(round(200 * scale))),
                               edge=max(24, int(round(200 * scale))))
        res.append((f, [(int(round(x * step)), sev, side, sig, rg)
                        for x, sev, side, sig, rg in cands]))
    return res


def group_stripes(scanned, tol_px=250):
    """把各块里"同一经度"的条纹聚成组——一个 Google 补丁边界往往横跨多块。

    support（有多少个不同块在同一经度上命中）是**唯一可靠的判据**：
    实测云影/地物边界单块也能有 40+ 灰阶的近垂直台阶，靠单块统计分不开。
    返回 [dict]：lon, side, support, severity, ref, x_ref, sig, rg_step, members
    按 severity 降序。
    """
    votes = []                                      # (lon, sev, side, sig, file, x, rg)
    for f, cands in scanned:
        g = block_geo(f)
        if g is None:
            continue
        for x, sev, side, sig, rg in cands:
            votes.append((px_to_lon(f, x), sev, side, sig, f, x, rg))
    if not votes:
        return []
    lat = (block_geo(votes[0][4])["lat_top"] + block_geo(votes[0][4])["lat_bot"]) / 2
    tol_deg = tol_px * 0.265 / m_per_deg_lon(lat)
    votes.sort(key=lambda t: t[0])
    groups = []
    for i in range(len(votes)):
        lo = votes[i][0]
        grp = [v for v in votes[i:] if v[0] - lo <= tol_deg]
        if not grp:
            continue
        # 同组里可能混了不同 lon 中心的候选，取被最多块支撑的那个子簇
        sides = [v[2] for v in grp]
        side = max(set(sides), key=sides.count)
        grp = [v for v in grp if v[2] == side]
        top = max(grp, key=lambda t: t[1])
        groups.append(dict(lon=float(np.median([v[0] for v in grp])), side=side,
                           support=len({v[4] for v in grp}), severity=top[1],
                           ref=top[4], x_ref=top[5], sig=top[3], rg_step=top[6],
                           members=grp))
    # 合并同一条线（组中心相近的取 severity 高的）
    groups.sort(key=lambda d: -d["severity"])
    kept = []
    for d in groups:
        if all(abs(d["lon"] - k["lon"]) > tol_deg for k in kept):
            kept.append(d)
    return kept


def plan_group(groups, args, files=None):
    """给每个组算出：参考块、逐带锚点、每带 LUT。

    两道保险，都是实测踩出来的：
      · 参考块只用"该组里 zoom 最高"的块——ZL13/15/16 填充块一个像素几米到十几米、
        窗口覆盖好几公里，估出来的 LUT 跟全分辨率块完全不是一回事（实测 ZL15 会把
        中灰 +45 灰阶并顶到 255 截断）。
      · 块内"接缝"游走 > max_wander_m，或校正量 > max_lut_shift 灰阶，都判为不像
        拼接线（更像云影/地物边），只警告不修。
    """
    plan = []
    for d in groups:
        cands = [d["ref"]] if d.get("ref") else [f for f in (files or []) if block_geo(f)]
        zs = [(block_geo(f) or {}).get("zl", 0) for f in cands]
        if zs:
            cands = [f for f, z in zip(cands, zs) if z == max(zs)]      # 只留最高 zoom
        pick = pick_reference(cands, d["lon"], args.band, args.search_px)
        if pick is None:
            continue
        ref, seed, ref_rows, side, score = pick
        if score < 3.0:
            print("  警告：参考块 %s 上这条接缝只有 %.1f 灰阶，可能是误检，跳过"
                  % (os.path.basename(ref), score))
            continue
        g_ref = block_geo(ref)
        mpx = g_ref["m_per_px"] if g_ref else 0.265
        wander_m = float(np.ptp(ref_rows)) * mpx
        if wander_m > args.max_wander_m:
            print("  警告：参考块 %s 里的\"接缝\"块内游走 %.0f m（> %.0f m），不像拼接线"
                  "（更像云影/地物边界），跳过不修"
                  % (os.path.basename(ref), wander_m, args.max_wander_m))
            continue
        img_ref = load_rgb(ref)
        q_warm, q_cool = band_anchors(img_ref, ref_rows, side, args.band, args.samples)
        luts = []
        for i in range(len(q_warm)):
            if args.mode == "identity":
                luts.append(None)
            elif args.mode == "offset":
                luts.append(offset_lut(q_warm[i][1], q_cool[i][1], args.strength))
            else:
                luts.append(quantile_lut(q_warm[i], q_cool[i],
                                         args.strength, args.slope_min, args.slope_max))
        shift = 0.0
        for k in (0, len(luts) // 2, len(luts) - 1):
            if luts[k] is None:
                continue
            for v in (32, 96, 160, 224):
                shift = max(shift, max(abs(float(luts[k][v][c]) - v) for c in range(3)))
        if shift > args.max_lut_shift:
            # 只是提示，不拦：实测真实接缝的逐带校正量能到 70+ 灰阶（中带 12、中位 31），
            # 因为它是"分位对分位"匹配、还要乘权重（实际生效量 ~1/3），幅度大不等于错。
            print("  注意：参考块 %s 某带校正量到 %.0f 灰阶（超过提示阈值 %.0f）；"
                  "实际生效量会乘权重，先看 --preview 再决定。"
                  % (os.path.basename(ref), shift, args.max_lut_shift))
        plan.append(dict(lon=d["lon"], side=side, ref=ref, ref_x=int(np.median(ref_rows)),
                         score=score, support=d["support"], severity=d["severity"],
                         sig=d["sig"], luts=luts, q_warm=q_warm, q_cool=q_cool,
                         wander=float(np.ptp(ref_rows)), wander_m=wander_m,
                         m_per_px=mpx))
    return plan


def groups_for_file(f, plan, reach_m, lat):
    """某个块要挨哪几刀（可能不止一条补丁边界）。

    受影响 = 块含这条经线，或块在它的西侧且东缘到它的距离 < reach（= full_m + ramp_m）。
    东侧的块权重恒为 0，一律不动。
    """
    g = block_geo(f, 4096)
    if g is None:
        return []
    out = []
    for p in plan:
        if g["lon_left"] <= p["lon"] <= g["lon_right"]:
            out.append(p)                                  # 线穿过这个块
            continue
        d_east_m = (p["lon"] - g["lon_right"]) * m_per_deg_lon(lat)   # >0: 块在接缝以西
        if 0.0 < d_east_m < reach_m:
            out.append(p)                                  # 块在西侧、且在衰减范围内
    return out


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


def apply_group_to_image(img, f, p, args):
    """把一条补丁边界的校正叠加到 img 上，返回 (out, weight, contains, seed)。

    权重以"逐带跟踪到的接缝线"为界硬切（几米抗锯齿），距离按米算 -> 跨块连续。
    """
    h, w, _ = img.shape
    gg = block_geo(f, w)
    if gg is None:                        # 文件名不合 Ortho4XP 规范，换不出经纬度
        return img, np.zeros((h, w), dtype=np.float32), False, 0.0
    m_per_px = gg["m_per_px"]
    seed = lon_to_px(f, p["lon"], w)
    contains = -64 <= seed <= w + 64
    if contains:
        seam_bands = track_seam(img, seed, search=args.search_px, band_rows=args.band)
        centres = (np.arange(len(seam_bands)) + 0.5) * args.band
        seam_rows = np.interp(np.arange(h), centres, seam_bands).astype(np.float32)
        dist_px = seam_rows[:, None] - np.arange(w, dtype=np.float32)[None, :]
    else:
        lons = gg["lon_left"] + np.arange(w, dtype=np.float32) / gg["px_per_deg"]
        dist_px = np.broadcast_to(((p["lon"] - lons) * gg["px_per_deg"])[None, :], (h, w)).copy()

    full_px = args.full_m / m_per_px
    ramp_px = args.ramp_m / m_per_px
    aa_px = max(2.0, args.aa_m / m_per_px)
    weight = np.clip(np.where(dist_px > full_px,
                              np.clip((full_px + ramp_px - dist_px) / ramp_px, 0, 1),
                              1.0), 0, 1)
    weight = np.where(dist_px < 0.0, 0.0, weight)              # 另一侧完全不动
    weight = np.where(np.abs(dist_px) < aa_px,                 # 接缝几米抗锯齿
                      np.clip(0.5 + dist_px / (2 * aa_px), 0, 1), weight).astype(np.float32)
    if p["side"] == "right":
        weight = 1.0 - weight

    if args.mode == "identity":
        r_px = int(max(8, round(2 * args.ramp_m / m_per_px)))
        band = np.abs(np.asarray(dist_px, dtype=np.float32)) < ramp_px
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
        corrected = apply_lut_rows(img, p["luts"], args.band)
        out = img * (1.0 - weight[:, :, None]) + corrected * weight[:, :, None]
    return out, weight, contains, seed


def fmt_side(s):
    return "西侧" if s == "left" else "东侧"


def _h(x):
    return html_mod.escape(str(x))


def write_scan_csv(path, rep):
    """机器可读清单：所有候选条纹 + 分组 + 修复计划。UTF-8 BOM，Excel 直接开。"""
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        wr = _csv.writer(fh)
        wr.writerow(["类别", "文件", "位置x", "向西距离m", "经度", "校正侧", "信号",
                     "R-G台阶", "强度", "同向性", "块数支撑", "分组经度"])
        for r in rep["rows"]:
            wr.writerow(["确认" if r["confirmed"] else "疑似", r["file"], r["x"],
                         "%.0f" % r["px_m"], "%.7f" % r["lon"], r["side"], r["sig"],
                         "%.1f" % r["rg"], "%.1f" % r["sev"],
                         ("%.0f%%" % (100 * r["coh"])) if r.get("coh") is not None else "",
                         r["support"],
                         "%.7f" % r["group"] if r["group"] is not None else ""])
        for i, d in enumerate(rep["groups"], 1):
            wr.writerow(["分组#%d" % i, os.path.basename(d["ref"] or ""), "", "",
                         "%.7f" % d["lon"], "", d["sig"], "%.1f" % d["rg_step"],
                         "%.1f" % d["severity"], d["support"], "%.7f" % d["lon"]])
        for name, ids in rep["todo"]:
            wr.writerow(["待修", name, "", "", "", "", "", "", "", "", ids])


def _jpeg_b64(im, quality=75):
    """PIL 图 -> base64 JPEG 字符串（内嵌进 HTML，报告单文件自带预览）。"""
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=int(quality), optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def line_coherence(arr, x, band=256, p_lo=32, p_hi=96):
    """这条 x 到底算不算"贯穿全高的竖直台阶"：逐带台阶同向比例。

    实测：真接缝 75-100%（每一带的色调跳变方向一致）；把地物/噪声当条纹时只有
    14-20%（带与带之间台阶方向来回翻）。所以这是判断"是不是一条线"的硬指标，
    比强度本身可靠——强度只看带间中位数，噪声也能凑出十几灰阶。
    返回 (同向比例, 逐带台阶中位数)。
    """
    h, w, _ = arr.shape
    n = h // band
    if n < 2 or w < 2 * p_hi:
        return 0.0, 0.0
    rg = arr[:n * band, :, 0].astype(np.float32) - arr[:n * band, :, 1].astype(np.float32)
    left = rg[:, max(0, x - p_hi):max(1, x - p_lo)].mean(axis=1)
    right = rg[:, min(w, x + p_lo):min(w, x + p_hi)].mean(axis=1)
    s = (right - left).reshape(n, band)
    per_band = np.median(s, axis=1)
    v = per_band[np.abs(per_band) > 2.0]
    if v.size == 0:
        return 0.0, 0.0
    return abs(float(np.mean(np.sign(v)))), float(np.median(np.abs(v)))


def preview_crops(path, xs_full, block=4096, thumb_px=320, detail_px=448, quality=72):
    """一次解码，给同一文件的多个候选位置各出两张预览：

      · 整块缩略图：一眼看出这条线在哪、走向如何；
      · 缝附近全分辨率裁切（纵向取块中部）：看清台阶是硬边（拼接线）还是软过渡（云影/地物）。
    两张都画一条 2px 红线标出算法认定的接缝位置。顺带在全分辨率上量"逐带同向性"。

    返回 {x_full: (缩略图 base64, 裁切图 base64, 同向性, 台阶中位数)}；打不开的文件返回 {}。
    """
    out = {}
    try:
        with Image.open(path) as im0:
            im = im0.convert("RGB")
        arr = np.asarray(im)
        w, h = im.size
        t = im.resize((thumb_px, thumb_px), Image.Resampling.LANCZOS)
        dt = ImageDraw.Draw(t)
        half = detail_px // 2
        for xf in sorted(set(int(x) for x in xs_full)):
            xs = max(0, min(w - 1, int(round(xf * w / float(block)))))
            tx = int(round(xs * thumb_px / float(w)))
            dt.line([(tx, 0), (tx, thumb_px)], fill=(255, 0, 0), width=2)
            left = max(0, min(max(0, w - detail_px), xs - half)) if w > detail_px else 0
            top = max(0, (h - detail_px) // 2)
            c = im.crop((left, top, left + min(detail_px, w), top + min(detail_px, h)))
            ImageDraw.Draw(c).line([(xs - left, 0), (xs - left, c.size[1])],
                                   fill=(255, 0, 0), width=2)
            coh, mag = line_coherence(arr, xs)
            out[xf] = (_jpeg_b64(t.copy(), quality), _jpeg_b64(c, quality), coh, mag)
    except Exception as e:                                   # 预览失败不该毁掉整个报告
        sys.stderr.write("预览生成失败 %s: %s\n" % (os.path.basename(path), e))
        return {}
    return out


def write_scan_html(path, rep):
    """白色底的独立 HTML 报告：确认条纹 / 疑似 / 分组 / 修复计划 / 修复结果。

    每条候选都内嵌两张预览图（整块缩略图 + 缝附近全分辨率裁切，红线标位置），
    点图放大。表头可点击排序。单文件自带，不依赖外部资源。
    """
    m = rep["meta"]
    zooms = "、".join("ZL%s×%d" % (z, n) for z, n in sorted(m["zooms"].items(), reverse=True))

    def table(cols, rows, cls=""):
        if not rows:
            return "<p class=\"none\">（无）</p>"
        th = "".join('<th onclick="srt(this,%d)">%s</th>' % (i, _h(c)) for i, c in enumerate(cols))
        body = []
        for r in rows:
            tds = []
            for c in r:
                if isinstance(c, tuple):
                    if c[0] is None:                        # 已经是整段 HTML（如预览图）
                        tds.append(c[1])
                    else:
                        tds.append("<td%s>%s</td>" % (c[1], _h(c[0])))
                else:
                    tds.append("<td>%s</td>" % _h(c))
            body.append("<tr>%s</tr>" % "".join(tds))
        return ('<table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                % (cls, th, "".join(body)))

    conf = [r for r in rep["rows"] if r["confirmed"]]
    susp = [r for r in rep["rows"] if not r["confirmed"]]

    # ---- 预览图：每个候选两张（整块缩略图 + 缝附近全分辨率裁切），按文件一次解码 ----
    # 同时在全分辨率上量"逐带同向性"——这是判断"到底是不是一条线"的硬指标。
    pv = {}
    if m.get("thumbs", True) and rep["rows"]:
        by_file = {}
        keep = sorted(rep["rows"], key=lambda r: -r["sev"])[:m["thumb_max"]]
        for r in keep:
            if r.get("path"):
                by_file.setdefault(r["path"], []).append(r["x"])
        for p, xs in by_file.items():
            pv[p] = preview_crops(p, xs, thumb_px=m["thumb_px"], detail_px=m["detail_px"],
                                  quality=m["thumb_q"])
        got = sum(len(v) for v in pv.values())
        print("  预览图：%d 个文件 / %d 条候选（点图放大）%s"
              % (len(pv), got, "" if got >= len(rep["rows"]) else
                 "；仅前 %d 条（--thumb-max 可调）" % m["thumb_max"]))

    # 用实测同向性重判"确认/疑似"：跨块支撑是必要条件，同向性是被测出来的充分性。
    reclass = 0
    for r in rep["rows"]:
        t = pv.get(r.get("path") or "", {}).get(r["x"])
        r["coh"] = t[2] if t else None
        r["coh_mag"] = t[3] if t else None
        if r["coh"] is not None:
            was = r["confirmed"]
            r["confirmed"] = bool(r["confirmed"] and r["coh"] >= m["min_coh"])
            if was and not r["confirmed"]:
                reclass += 1
    if reclass:
        print("  同向性复核：%d 条原判\"确认\"因逐带方向不一致改判为疑似"
              "（同向性 < %.0f%%）" % (reclass, 100 * m["min_coh"]))
    conf = [r for r in rep["rows"] if r["confirmed"]]
    susp = [r for r in rep["rows"] if not r["confirmed"]]

    qa_cols = (["预览"] if pv else []) + [
        "强度", "同向性", "文件", "位置 x", "向西距离(m)", "经度", "校正侧", "信号",
        "R-G 台阶", "块数支撑"]

    def qa_rows(rs):
        rows = []
        for r in sorted(rs, key=lambda r: -r["sev"]):
            cells = []
            if pv:
                t = pv.get(r.get("path") or "", {}).get(r["x"])
                if t:
                    cells.append((None, '<td class="pv">'
                                  '<img class="t" width="176" title="整块（红线=接缝位置）" alt="整块" '
                                  'src="data:image/jpeg;base64,%s" onclick="big(this)">'
                                  '<img class="d" width="176" title="缝附近全分辨率" alt="缝附近" '
                                  'src="data:image/jpeg;base64,%s" onclick="big(this)">'
                                  '</td>' % (t[0], t[1])))
                else:
                    cells.append("—")
            coh = "—" if r["coh"] is None else "%.0f%%" % (100 * r["coh"])
            cells += [("%.1f" % r["sev"]), coh, r["file"], r["x"], "%.0f" % r["px_m"],
                      "%.7f" % r["lon"], r["side"], r["sig"], "%+.1f" % r["rg"],
                      (r["support"] if r["confirmed"] else "—")]
            rows.append(cells)
        return rows

    grp_rows = []
    for i, d in enumerate(rep["groups"], 1):
        ok = rep["fix_ids"].get(i, False)
        grp_rows.append(["#%d" % i, "%.7f" % d["lon"], "%.1f" % d["severity"], d["support"],
                         "西侧" if d["side"] == "left" else "东侧", d["sig"],
                         "%+.1f" % d["rg_step"], os.path.basename(d["ref"] or ""),
                         ("自动修" if ok else "只报告"),
                         ("%.0f" % d["wander_m"]) if ok and d.get("wander_m") is not None else "—"])

    todo_rows = [[n, ids] for n, ids in rep["todo"]]
    fix_rows = [[r["file"], r["before"], r["after"], r["weight"]] for r in rep["fixed"]]

    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Google 影像拼接条纹扫描报告</title>
<style>
 body{background:#ffffff;color:#1a1a1a;font:14px/1.6 -apple-system,"Helvetica Neue",
      "PingFang SC",sans-serif;margin:0;padding:28px 32px;}
 h1{font-size:20px;margin:0 0 4px;} h2{font-size:15px;margin:26px 0 8px;
      padding-bottom:5px;border-bottom:1px solid #e2e2e2;}
 .sub{color:#666;font-size:12.5px;margin-bottom:18px;}
 .meta{background:#fafafa;border:1px solid #e6e6e6;border-radius:6px;padding:12px 16px;
      font-size:13px;margin-bottom:6px;}
 .meta b{color:#111;} .meta span{margin-right:26px;white-space:nowrap;display:inline-block;}
 table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:6px;}
 th{background:#f4f4f4;border:1px solid #e0e0e0;padding:6px 8px;text-align:left;
      cursor:pointer;user-select:none;white-space:nowrap;font-weight:600;}
 th:hover{background:#ececec;} th:after{content:" ⇅";color:#bbb;font-weight:400;}
 td{border:1px solid #e6e6e6;padding:5px 8px;white-space:nowrap;}
 tbody tr:nth-child(even){background:#fbfbfb;}
 td:last-child{white-space:normal;}
 .none{color:#888;font-size:13px;margin:6px 0 0;}
 .warn{background:#fff8e6;border-left:3px solid #e0a800;padding:10px 14px;border-radius:4px;
      font-size:13px;margin-top:8px;}
 .ok{background:#f1f8f2;border-left:3px solid #2e7d32;padding:10px 14px;border-radius:4px;
      font-size:13px;margin-top:8px;}
 code{background:#f5f5f5;padding:1px 5px;border-radius:3px;
      font:12.5px ui-monospace,Menlo,monospace;}
 .sig-rg{color:#0b6b3a;font-weight:600;} .sig-y{color:#8a6d00;}
 td.pv{padding:3px;background:#fff;white-space:nowrap;} td.pv img{display:inline-block;
      vertical-align:top;border:1px solid #ddd;border-radius:3px;cursor:zoom-in;}
 td.pv img.t{margin-right:3px;}
 #ov{display:none;position:fixed;inset:0;background:rgba(20,20,20,.82);z-index:9;
      align-items:center;justify-content:center;cursor:zoom-out;}
 #ov img{max-width:96vw;max-height:94vh;background:#fff;border-radius:4px;}
 #ov span{position:fixed;top:14px;right:22px;color:#fff;font-size:26px;cursor:pointer;
      line-height:1;}
 footer{color:#888;font-size:12px;margin-top:26px;border-top:1px solid #e6e6e6;padding-top:10px;}
</style></head><body>
<h1>Google 影像拼接条纹扫描报告</h1>
<div class="sub">Ortho4XP 地景切片 · GO2 卫星影像马赛克补丁边界检测（只读扫描，未改动任何文件）</div>""")

    parts.append('<div class="meta"><span><b>目录</b> %s</span><span><b>文件</b> %d（%s）</span>'
                 '<span><b>zoom</b> %s</span><br style="line-height:2">'
                 '<span><b>阈值</b> ≥%.1f 灰阶</span><span><b>聚组容差</b> %d px</span>'
                 '<span><b>列表门槛</b> ≥%d 块</span><span><b>自动修门槛</b> ≥%d 块</span>'
                 '<span><b>接缝游走上限</b> %.0f m</span>'
                 '<span><b>扫描时间</b> %s</span></div>'
                 % (_h(m["src"]), m["n_files"], _h(m["exts"]), _h(zooms), m["min_step"],
                    m["group_px"], m["min_support"], m["fix_min_support"],
                    m["max_wander_m"], _h(m["time"])))

    parts.append('<h2>① 确认为拼接条纹（同一经度被多个块支撑）%d 处</h2>' % len(conf))
    parts.append('''<div class="warn"><b>判据：跨块支撑。</b>单块图像上，Google 补丁边界与云影/地物
边界几乎无法区分（实测云影台阶可达 43 灰阶，和真接缝的 28 灰阶一样陡）。只有
「同一条经线被多个块同时命中」才是可靠证据。信号 <span class="sig-rg">rg</span> = R−G 色偏跳变，
<span class="sig-y">y</span> = 亮度跳变。R−G 台阶是这条线的色偏分量。<br>
<b>同向性</b> = 逐带（每 256 行）台阶方向一致的比例，在全分辨率上实测：真接缝 <b>75–100%</b>，
噪声/地物凑出来的假线 <b>&lt;35%</b>（带与带之间方向来回翻）。判定「确认」要求跨块支撑
<b>且</b> 同向性 ≥ __MINCOH__%。<br>
<b>看预览图判断：</b>每行两张 —— 左 = 整块缩略图（红线 = 算法认定的接缝位置，看它落在哪、
走向如何），右 = 缝附近的全分辨率裁切（看台阶是硬边还是软过渡）。点任意一张放大。<br>
硬边、两侧纹理连续但色调/色偏跳变 = 拼接线；软过渡、边缘跟着地形地物走 = 云影或地物边。</div>'''.replace(
        "__MINCOH__", "%.0f" % (100 * m["min_coh"])))
    parts.append(table(qa_cols, qa_rows(conf)))

    parts.append('<h2>② 疑似（仅单块命中，未自动修复）%d 处</h2>' % len(susp))
    parts.append('<p class="sub" style="margin:6px 0 0">这些也可能是真接缝（补丁角点可能落在一块内），'
                 '但也可能是云影边界、地物边缘。要修的话用 <code>--seam-lon &lt;经度&gt;</code> 手工指定。</p>')
    parts.append(table(qa_cols, qa_rows(susp)))

    parts.append('<h2>③ 分组（同一经线跨块）%d 组</h2>' % len(rep["groups"]))
    parts.append(table(["组", "经度", "强度", "块数支撑", "被校正侧", "信号", "R-G 台阶",
                        "参考块", "处置", "参考块内游走(m)"], grp_rows))

    parts.append('<h2>④ 修复计划：文件清单（%d 个）</h2>' % len(todo_rows))
    parts.append('<p class="sub" style="margin:6px 0 0">受影响 = 该块含这条经线，'
                 '或该块在它西侧且东缘到它的距离小于衰减范围（%d m）。东侧的块权重恒为 0，一律不动。</p>'
                 % m["reach_m"])
    parts.append(table(["文件", "挨哪几刀"], todo_rows))

    if fix_rows:
        parts.append('<h2>⑤ 修复结果（跨缝 Δ(R−G)，灰阶）</h2>')
        parts.append(table(["文件", "修复前", "修复后", "校正权重均值"], fix_rows))
    else:
        parts.append('<h2>⑤ 修复结果</h2><p class="none">本次为只读扫描，未写入文件。'
                     '加 <code>--write</code> 才会执行。</p>')

    parts.append('''<h2>⑥ 说明与已知边界</h2>
<div class="meta" style="line-height:1.9">
· <b>不改格式</b>：输出保持原扩展名原格式（jpg→同名 jpg，dds→同名 dds 走同一条
  <code>nvcompress -bc1</code>），不做格式转换。<br>
· <b>不碰原文件</b>：给了 <code>--out-dir</code> 就写那里；否则原地写并把原文件备份成
  <code>.bak</code>。<br>
· <b>只信跨块</b>：见 ①。单块命中的一律只列不修，避免把云影边界当接缝改掉。<br>
· <b>低 ZL 填充块</b>：ZL13/15/16 块一个像素几米到十几米、窗口覆盖好几公里，估出的 tone
  LUT 与全分辨率块不是一回事，所以参考块只从「该组里 zoom 最高」的块里挑。<br>
· <b>接缝游走</b>：参考块里那条线自身游走超过 %.0f m，判定为云影/地物边，跳过不修。<br>
· <b>校正量幅度不等于错</b>：真实接缝的逐带校正量能到 70+ 灰阶（分位对分位匹配后还要乘
  权重，实际生效量约 1/3），所以幅度只提示不拦。<br>
· <b>建议流程</b>：先看本报告 → 挑出要修的经度 → <code>--seam-lon</code> 或默认自动 → 加
  <code>--preview</code> 看一张 → 再加 <code>--write</code>。
</div>
<footer>由 Ortho4XP <code>install/fix_google_seam.py</code> 生成 · 点表头可排序 · 点预览图放大</footer>
<div id="ov" onclick="this.style.display='none'"><span>×</span><img alt="放大预览" src=""></div>
<script>
function big(el){var im=el;
 if(el.classList.contains('t')&&el.nextElementSibling)im=el.nextElementSibling;   /* 缩略图 -> 用同行的缝附近图 */
 var o=document.getElementById('ov');
 o.querySelector('img').src=im.src;o.style.display='flex';}
function srt(th,ci){var tb=th.closest('table').tBodies[0],rs=[].slice.call(tb.rows);
 var d=th.dataset.d==='1'?-1:1;th.dataset.d=th.dataset.d==='1'?'0':'1';
 rs.sort(function(a,b){var x=a.cells[ci].innerText.trim(),y=b.cells[ci].innerText.trim();
 var nx=parseFloat(x.replace(/[^\\d.eE+-]/g,'')),ny=parseFloat(y.replace(/[^\\d.eE+-]/g,''));
 if(isFinite(nx)&&isFinite(ny))return d*(nx-ny);
 return d*x.localeCompare(y,'zh');});
 rs.forEach(function(r){tb.appendChild(r);});}
</script>
</body></html>''' % m["max_wander_m"])

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print("  扫描报告：%s" % path)


def main():
    ap = argparse.ArgumentParser(
        description="扫描并修掉 Google(GO2) 影像拼接造成的竖直黄条纹 / 色调台阶")
    ap.add_argument("--in", dest="single")
    ap.add_argument("--dir", dest="folder")
    ap.add_argument("--pattern", default="*_GO2*", help="默认同时匹配 jpg/dds")
    ap.add_argument("--recursive", action="store_true", help="在 --dir 下递归查找（Orthophotos 子目录）")
    ap.add_argument("--scan", action="store_true",
                    help="只扫描列出条纹文件，不改动任何文件（优先于 --write）")
    ap.add_argument("--seam-x", default="auto")
    ap.add_argument("--seam-lon", type=float, default=None, help="手工指定接缝经度（跳过自动分组）")
    ap.add_argument("--strength", type=float, default=0.7)
    ap.add_argument("--mode", choices=["match", "offset", "identity"], default="match")
    ap.add_argument("--band", type=int, default=256, help="分带行数")
    ap.add_argument("--full-m", type=float, default=250.0, help="接缝以西多少米内保持 100%% 校正")
    ap.add_argument("--ramp-m", type=float, default=700.0, help="再往西多少米内把校正量渐变到 0")
    ap.add_argument("--feather-m", type=float, default=8.0, help="保留参数（等价于 --aa-m）")
    ap.add_argument("--samples", type=int, default=420, help="接缝两侧取样窗宽(px)")
    ap.add_argument("--min-step", type=float, default=6.0,
                    help="判定条纹的最小台阶（灰阶）；扫描和报告都用它")
    ap.add_argument("--group-px", type=int, default=250, help="同一经线的聚组容差(px)")
    ap.add_argument("--min-support", type=int, default=2,
                    help="列为\"确认条纹\"所需的最少块数支撑（1 块的疑似只报告不修）")
    ap.add_argument("--fix-min-support", type=int, default=3,
                    help="自动修复所需的最少块数支撑；比列表阈值更严，避免把长的云影边界当接缝改掉")
    ap.add_argument("--export-html", metavar="PATH",
                    help="把扫描结果导出成可排序的 HTML 报告（白色底，内嵌预览图）")
    ap.add_argument("--export-csv", metavar="PATH",
                    help="把扫描结果导出成 CSV（UTF-8 BOM，Excel 直接开）")
    ap.add_argument("--no-thumbs", action="store_true",
                    help="HTML 报告不内嵌预览图（默认内嵌：整块缩略图 + 缝附近全分辨率裁切）")
    ap.add_argument("--thumb-px", type=int, default=320, help="整块缩略图边长(px)")
    ap.add_argument("--detail-px", type=int, default=448, help="缝附近全分辨率裁切图边长(px)")
    ap.add_argument("--thumb-q", type=int, default=72, help="预览图 JPEG 质量(1-95)")
    ap.add_argument("--min-coh", type=float, default=0.5,
                    help="判定\"确实是一条线\"所需的逐带同向性（真接缝实测 75-100%%，噪声 <35%%）")
    ap.add_argument("--thumb-max", type=int, default=200,
                    help="最多内嵌多少条候选的预览图（按强度排序取前 N，防止目录巨大时报告爆掉）")
    ap.add_argument("--max-wander-m", type=float, default=400.0,
                    help="参考块内接缝游走上限(米)；超过判定为云影/地物边，不修")
    ap.add_argument("--max-lut-shift", type=float, default=60.0,
                    help="校正量提示阈值(灰阶)；超过只提示不拦（真实接缝也能到 70+）")
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

    geo0 = block_geo(next((f for f in files if block_geo(f)), files[0]))
    lat_mid = (geo0["lat_top"] + geo0["lat_bot"]) / 2

    # ---- 1. 扫描：逐文件找"贯穿全高"的竖直台阶 ----
    print("\n== 扫描条纹（阈值 %.1f 灰阶，检测在 1/4 缩图上做）==" % args.min_step)
    scanned = scan_files(files, band=args.band, min_step=args.min_step)
    geo_cache = {f: block_geo(f) for f, _ in scanned}

    # 分组要在报告之前做：是否"真条纹"由跨块支撑决定，不是单块的自评分
    if args.seam_lon is not None:
        groups = [dict(lon=args.seam_lon, side="left", support=0, severity=0.0,
                       ref=None, x_ref=0, sig="手工", rg_step=0.0, members=[])]
    else:
        groups = group_stripes(scanned, tol_px=args.group_px)
    live = [d for d in groups if args.seam_lon is not None or d["support"] >= args.min_support]

    def confirmed_lon(f, x):
        """这条候选落在某个"被多块支撑"的经线上吗？"""
        g = geo_cache.get(f)
        if g is None:
            return None
        lat_m = (g["lat_top"] + g["lat_bot"]) / 2
        for d in live:
            if abs(px_to_lon(f, x) - d["lon"]) * m_per_deg_lon(lat_m) < 200:
                return d
        return None

    confirmed, suspect = [], []
    rows = []
    for f, cands in scanned:
        g = geo_cache.get(f)
        for (x, sev, side, sig, rg) in cands:
            d = confirmed_lon(f, x)
            (confirmed if d else suspect).append((f, (x, sev, side, sig, rg)))
            rows.append(dict(file=os.path.basename(f), path=f, x=int(x),
                             px_m=(x * g["m_per_px"]) if g else 0.0,
                             lon=px_to_lon(f, x) if g else float("nan"),
                             side=fmt_side(side), sig=sig, rg=float(rg), sev=float(sev),
                             support=int(d["support"]) if d else 0,
                             group=(d["lon"] if d else None), confirmed=bool(d)))

    rep: dict = dict(meta=dict(src=os.path.abspath(args.folder or os.path.dirname(files[0])),
                         n_files=len(files), exts="/".join(exts),
                         zooms=collections.Counter((block_geo(f) or {}).get("zl", 0)
                                                   for f in files),
                         min_step=args.min_step, group_px=args.group_px,
                         min_support=args.min_support, fix_min_support=args.fix_min_support,
                         max_wander_m=args.max_wander_m, reach_m=int(args.full_m + args.ramp_m),
                         thumbs=not args.no_thumbs, thumb_px=args.thumb_px,
                         detail_px=args.detail_px, thumb_q=args.thumb_q,
                         min_coh=args.min_coh, thumb_max=args.thumb_max,
                         time=time.strftime("%Y-%m-%d %H:%M:%S")),
               rows=rows, groups=[], plan=[], todo=[], fixed=[], fix_ids={})

    def emit():
        if args.export_html:
            write_scan_html(args.export_html, rep)
        if args.export_csv:
            write_scan_csv(args.export_csv, rep)

    def show(rows, tag):
        if not rows:
            return
        print("\n%s %d 处：" % (tag, len(rows)))
        print("  强度   文件                                 位置           经度           校正侧  信号  R-G台阶  支撑")
        for f, (x, sev, side, sig, rg) in sorted(rows, key=lambda t: -t[1][1]):
            g = geo_cache.get(f)
            d = confirmed_lon(f, x)
            sup = ("%d 块" % d["support"]) if (d and d["support"]) else ""
            mp = g["m_per_px"] if g else 0.0
            print("  %5.1f  %-34s x=%-5d %5.0fm  %.7f   %s    %-3s  %+6.1f   %s"
                  % (sev, os.path.basename(f), x, x * mp,
                     px_to_lon(f, x) if g else float("nan"),
                     fmt_side(side), sig, rg, sup))

    if confirmed:
        show(confirmed, "确认为拼接条纹（同一经度被多个块支撑）")
    elif not suspect:
        print("没有发现贯穿全高的竖直台阶（全部 %d 个文件都干净）" % len(files))
    if suspect:
        show(suspect, "疑似（仅单块命中；实测云影/地物边界也能到 40+ 灰阶，靠单块分不开，"
                      "故默认不自动修）")

    # ---- 2. 按经线聚组：一个 Google 补丁边界常常横跨多个块 ----
    rep["groups"] = groups
    if not groups:
        print("\n没有需要处理的拼接边界。")
        emit()
        return
    print("\n== 拼接边界分组（同一经线跨块）==")
    usable = []
    for i, d in enumerate(groups, 1):
        ok = args.seam_lon is not None or d["support"] >= args.fix_min_support
        rep["fix_ids"][i] = bool(ok)
        print("  #%d  lon=%.7f  强度 %5.1f  支撑 %d 块  校正 %s  参考 %s  信号 %s (R-G %+.1f)%s"
              % (i, d["lon"], d["severity"], d["support"], fmt_side(d["side"]),
                 os.path.basename(d["ref"]) if d["ref"] else "自动挑选", d["sig"], d["rg_step"],
                 "" if ok else "   <- 支撑 < %d，只报告不自动修" % args.fix_min_support))
        if ok:
            usable.append(d)
    if not usable:
        print("\n没有达到自动修复门槛（支撑 >= %d）的拼接边界；可加 --fix-min-support 放宽，"
              "或用 --seam-lon 手工指定经度。" % args.fix_min_support)
        emit()
        return
    groups = usable

    # ---- 3. 修复计划：参考块 -> 逐带锚点 -> LUT；哪些块要挨哪几刀 ----
    plan = plan_group(groups, args, files)
    if not plan:
        print("\n检出的条纹证据不足，未生成修复计划（可用 --seam-lon 手工指定经度）。")
        emit()
        return
    for i, p in enumerate(plan, 1):
        p["id"] = i
        print("\n  计划 #%d  lon=%.7f  参考块 %s（接缝对比度 %.1f 灰阶，块内游走 %.0f px / %.0f m）"
              % (i, p["lon"], os.path.basename(p["ref"]), p["score"],
                 p["wander"], p["wander"] * p["m_per_px"]))
        print("    校正侧 %s，接缝在参考块内 x=%d px，%d 个纬度带"
              % (fmt_side(p["side"]), p["ref_x"], len(p["luts"])))
        if p["luts"] and p["luts"][0] is not None:
            lut = p["luts"][len(p["luts"]) // 2]
            print("    LUT(中带, 强度 %.2f) in->out: %s"
                  % (args.strength, "  ".join("%d->%s" % (v, np.round(lut[v], 0).tolist())
                                              for v in (32, 96, 160, 224))))
        print("    黄侧 带0 p10/50/90: %s" % np.round(p["q_warm"][0], 0).tolist())
        print("    绿侧 带0 p10/50/90: %s" % np.round(p["q_cool"][0], 0).tolist())

    reach_m = args.full_m + args.ramp_m
    todo, skipped = [], []
    for f in files:
        aff = groups_for_file(f, plan, reach_m, lat_mid)
        (todo if aff else skipped).append((f, aff))
    print("\n== 修复计划：文件清单 ==")
    for f, aff in todo:
        print("  改  %-34s <- %s" % (os.path.basename(f),
                                     "、".join("#%d(lon=%.7f)" % (p["id"], p["lon"]) for p in aff)))
    if skipped:
        print("  跳过 %d 个文件（不在任何边界西侧 %d m 内）：%s"
              % (len(skipped), int(reach_m),
                 ", ".join(os.path.basename(f) for f, _ in skipped[:6]) +
                 (" ..." if len(skipped) > 6 else "")))
    rep["plan"] = plan
    rep["todo"] = [(os.path.basename(f),
                    "、".join("#%d(lon=%.7f)" % (p["id"], p["lon"]) for p in aff))
                   for f, aff in todo]

    if args.scan or not args.write_dds:
        print("\n只扫描未改动文件。加 --write 执行修复（--out-dir 换目录，否则原地并留 .bak）。")
        emit()
        return

    # ---- 4. 执行 ----
    print("\n== 修复 ==")
    done = 0
    for f, aff in todo:
        img = load_rgb(f)
        cur = img
        lines = []
        for p in aff:
            cur, weight, contains, seed = apply_group_to_image(cur, f, p, args)
            sx = int(seed) if contains else img.shape[1]
            before, after = fmt(step_across(img, sx)), fmt(step_across(cur, sx))
            lines.append("[%s] #%d 跨缝 Δ(R-G): %s -> %s   校正权重均值 %.0f%%%s"
                         % (os.path.basename(f), p["id"], before, after, 100 * weight.mean(),
                            "" if contains else "   （此块全在接缝以西）"))
            rep["fixed"].append(dict(file=os.path.basename(f), before=before, after=after,
                                     weight="%.0f%%" % (100 * weight.mean())))
        out = np.clip(cur, 0, 255)
        for ln in lines:
            print(ln)
        if args.preview:
            pp = args.preview if len(files) == 1 else os.path.join(
                os.path.dirname(args.preview), os.path.basename(f)[:-4] + "_prev.png")
            make_preview(img, out, int(lon_to_px(f, plan[0]["lon"], img.shape[1])), pp)
        out_u8 = out.astype(np.uint8)
        if args.out_dir:
            os.makedirs(args.out_dir, exist_ok=True)
            dst = os.path.join(args.out_dir, os.path.basename(f))   # 同名同扩展名
        else:
            dst = f
            if not os.path.exists(f + ".bak"):
                shutil.copy2(f, f + ".bak")
        save_like(out_u8, dst, jpeg_quality=args.jpeg_quality,
                  nvcompress=args.nvcompress, fast=args.fast)
        done += 1
    print("\n处理 %d 个文件（扩展名/格式不变）%s"
          % (done, "，输出目录 %s" % args.out_dir if args.out_dir else "，原文件已备份为 .bak"))
    emit()


if __name__ == "__main__":
    main()