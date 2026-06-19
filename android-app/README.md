# Chhotu Audiobooks Android

Simple personal-use Android client for the local Flask audiobook server.

- Server URL is compiled as `http://192.168.70.164:5078`.
- Lists the server library.
- Searches AudioBookBay via the Flask server.
- Adds books through the server/Real-Debrid flow.
- Streams audio with Android `MediaPlayer`.
- Saves files with Android `DownloadManager` into Downloads/Audiobooks.

Build:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$HOME/gradle-8.10.2/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
gradle :app:assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`
