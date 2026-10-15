# Third-party notices

The application bundles a CPython runtime under the Python Software Foundation License; the full license is included in licenses/Python-LICENSE.txt and the built app’s ThirdPartyLicenses directory. CPython: https://www.python.org/ .

The backend is packaged using PyInstaller 6.22.3. The PyInstaller bootloader is distributed under GPL-2.0-or-later with the bootloader exception, which allows packaged applications to use their own licenses. Its license is copied into the built app. PyInstaller: https://pyinstaller.org/ . Build-time dependencies are pinned in requirements-build.txt; their analysis tools are not a replacement for Claude or its native modules.

The MIT license in this repository covers this project's code only. No Anthropic application files are distributed. macOS system frameworks and system-provided menu symbols are used through Apple's platform APIs.
