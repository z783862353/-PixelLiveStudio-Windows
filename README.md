# Pixel Live Studio — Windows Cloud Build

This repository builds a Windows x64 single-file executable with GitHub Actions. The user's PC does not need Python, Visual Studio, or a separately installed FFmpeg.

## Build it in GitHub (one-time setup)

1. Create a GitHub repository named `PixelLiveStudio` under your account. Choose **Private** if you do not want the source code to be public.
2. Open the new repository and choose **Add file → Upload files**.
3. Extract this ZIP on your PC. Open the extracted `PixelLiveStudio_CloudBuild` folder, select **all** its contents (including the `.github` folder), and drag them into GitHub's upload area.
4. Commit the upload to the repository's default branch (`main` or `master`).
5. Open the **Actions** tab and wait for **Build Pixel Live Studio for Windows** to finish with a green check.
6. Open the repository's **Releases** page. Download `PixelLiveStudio.exe` directly. The ZIP asset is an alternative containing the same EXE.

The workflow also creates a downloadable Actions artifact named `PixelLiveStudio-Windows-x64` (available for 14 days).

## What the build includes

- Python runtime and Tk GUI bundled by PyInstaller into one EXE.
- `ffmpeg.exe` and `ffprobe.exe` bundled inside the EXE.
- MP4/MOV/M4V/MKV batch import.
- 1–4 parallel conversion tasks.
- Configurable start time, clip duration, frame rate, width, and quality.
- JPEG primary image + appended MP4 motion segment + Android Motion Photo XMP metadata.

## Important compatibility note

The app creates an Android Motion Photo-style JPEG container and verifies that the appended MP4 can be read. This does **not** prove that a particular Samsung Gallery or Douyin version will accept it. Test on the target phone first; Douyin recognition is not guaranteed until a real upload test succeeds.

## Third-party software

The workflow downloads the Windows FFmpeg Essentials build from Gyan Doshi's Windows build distribution at `https://www.gyan.dev/ffmpeg/builds/`. The distributed FFmpeg binary is a separate executable launched by this app. See FFmpeg's license information at `https://www.ffmpeg.org/legal.html` and the build distributor's page for the exact build details and corresponding source links.

## Troubleshooting

- If the workflow does not start, open **Actions** and check whether GitHub asks you to enable Actions for the repository.
- If the workflow fails, open the failed run and inspect the red step. The most likely external dependency is the FFmpeg download URL or a third-party action version; no local installation is needed to resolve those workflow issues.
- Windows may show a SmartScreen warning because this first build is not code-signed. Only run the file downloaded from your own repository's Releases page.
