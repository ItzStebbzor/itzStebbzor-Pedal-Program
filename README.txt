STEBBZOR PEDAL MANAGER v1.4.2
================================

Update checker is preconfigured for:
https://github.com/ItzStebbzor/itzStebbzor-Pedal-Program

In the app, open APPLICATION UPDATES and confirm the repository field is:
ItzStebbzor/itzStebbzor-Pedal-Program

PUBLISHING FUTURE UPDATES
-------------------------
The update checker uses the GitHub Releases API. For it to work:
1. The repository must be public (or the app must be extended with authenticated access).
2. Publish a GitHub Release with a tag such as v1.4.3.
3. Build the Windows app using build_exe.bat.
4. ZIP the CONTENTS of dist\StebbzorPedalManager into an archive named exactly:
   StebbzorPedalManager-Windows.zip
5. Attach that ZIP to the GitHub Release.

The release ZIP must contain StebbzorPedalManager.exe and all the PyInstaller
support files from the onedir folder. The update checker downloads and applies
that archive when running as the compiled EXE. Running from source can check
for releases but cannot automatically replace the source installation.

INSTALL / TEST
--------------
Run install.bat, then start.bat to test from source.
Run build_exe.bat on Windows to compile the standalone EXE.
Profiles and settings are stored separately under %APPDATA%\StebbzorPedalManager.
