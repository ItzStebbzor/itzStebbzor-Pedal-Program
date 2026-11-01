STEBBZOR PEDAL MANAGER v1.4.0
===============================

New in v1.4:
- Built-in GitHub Releases update checker.
- Optional automatic update check on startup.
- Downloads and installs a newer release for the compiled Windows EXE.
- Existing profiles/settings remain in %APPDATA%\StebbzorPedalManager.

IMPORTANT UPDATE-CHECKER SETUP
------------------------------
The update checker needs a GitHub repository that publishes the app's releases.
No repository has been configured for this project yet.

1. Create a public GitHub repository for Stebbzor Pedal Manager.
2. In the app's APPLICATION UPDATES section, enter owner/repository.
3. Click Save, then Check for updates.
4. To publish a release, build the app using build_exe.bat.
5. ZIP the CONTENTS of dist\StebbzorPedalManager (the EXE and all support files)
   into a file named exactly:
   StebbzorPedalManager-Windows.zip
6. Create a GitHub Release with a tag such as v1.4.1 and attach that ZIP asset.
7. Future releases can be detected and installed from inside the application.

The app uses the GitHub Releases API. The update archive must contain
StebbzorPedalManager.exe plus the complete PyInstaller onedir support files.
Automatic in-place installation is enabled only when running the compiled EXE.
When running from Python source, the checker can detect releases but cannot
replace the source installation automatically.

INSTALL / TEST
--------------
- Run install.bat, then start.bat to test from source.
- Run build_exe.bat on Windows to produce the standalone application.
- Run the EXE from dist\StebbzorPedalManager.
- Enter your GitHub repository under APPLICATION UPDATES.

Other features retained: pedal remapping, Hold/Toggle, saved profiles,
game detection, Windows startup, and system tray support.
