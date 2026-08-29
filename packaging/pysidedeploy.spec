[app]
title = iOS Developer Toolkit
project_dir = ..
input_file = packaging/main.py
exec_directory = build/release
project_file =
icon = macos/iOSDeveloperToolkit.icns

[python]
python_path =
packages = Nuitka==4.1.1
android_packages =

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = platforms,imageformats

[android]
wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]
macos.permissions =
mode = standalone
extra_args = --quiet --assume-yes-for-downloads --noinclude-qt-translations --include-package=ios_developer_toolkit --include-package=pymobiledevice3 --include-package=developer_disk_image --include-data-dir=ios_developer_toolkit/assets=ios_developer_toolkit/assets --include-package-data=pymobiledevice3 --include-package-data=developer_disk_image --nofollow-import-to=IPython --nofollow-import-to=jedi --nofollow-import-to=xonsh --macos-app-name="iOS Developer Toolkit" --macos-app-version=0.3.0 --macos-app-mode=gui

[buildozer]
mode = release
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch =
