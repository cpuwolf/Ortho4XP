set PYROOT=E:\Python310
set PATH=%PYROOT%;%PYROOT%\Scripts;%PATH%;

set MYROOT=%~dp0..\
pushd %MYROOT%

python.exe -m pip install --upgrade pip

pip3 install pyproj

:: Install other dependencies
pip3 install numpy shapely rtree pillow requests scikit-fmm

::python -c "import sys; print(sys.version); print(sys.maxsize > 2**32)"
::pip3 install "D:\x-plane resources\ortho4xp_allinone\gdal-3.10.2-cp310-cp310-win_amd64.whl"

::pip3 freeze > requirements.txt
::pip3 install pipreqs
::pipreqs .

pip3 install requests


