# Chhotu Audiobooks Android

Simple personal-use Android wrapper for the local Flask audiobook web app.

- Server URL is compiled as `http://192.168.70.164:5078`.
- The APK opens the existing Flask web UI in Android WebView so the mobile app matches the webapp design.
- JavaScript and DOM storage are enabled for the web UI.
- External links open in the Android browser.
- Web downloads are handed to Android `DownloadManager` into Downloads/Audiobooks.
- Real-Debrid and Audiobookshelf tokens stay server-side; the APK contains only the private server URL.

Build:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$HOME/gradle-8.10.2/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
gradle :app:assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`
