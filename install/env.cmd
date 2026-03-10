set PYROOT=E:\Python310
set PATH=%PYROOT%;%PYROOT%\Scripts;%PATH%;

set MYROOT=%~dp0..\
pushd %MYROOT%

python.exe -m pip install --upgrade pip

pip3 install pyproj

# Install other dependencies
pip3 install numpy shapely rtree pillow requests scikit-fmm

#pip3 freeze > requirements.txt
pip3 install pipreqs

pipreqs .



