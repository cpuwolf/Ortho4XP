call env.cmd

::python.exe install\fix_google_seam.py --dir "Orthophotos\+20+080\+27+089\GO2_19" --scan

python.exe install\fix_google_seam.py --dir "Orthophotos\+20+080\+27+089\GO2_19" --recursive --write --out-dir "tmp\fixed"


pause