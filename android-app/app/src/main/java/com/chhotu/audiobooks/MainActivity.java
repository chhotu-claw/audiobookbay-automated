package com.chhotu.audiobooks;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String SERVER_URL = BuildConfig.SERVER_URL;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private LinearLayout content;
    private TextView status;
    private TextView absStatus;
    private ProgressBar spinner;
    private EditText searchBox;
    private MediaPlayer player;
    private String currentTitle = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildLayout();
        loadAudiobookshelfStatus();
        loadLibrary();
    }

    private void buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(14), dp(16), dp(14));
        root.setBackgroundColor(0xfff6f3ed);

        TextView title = new TextView(this);
        title.setText("Chhotu Audiobooks");
        title.setTextSize(24);
        title.setTextColor(0xff111111);
        title.setGravity(Gravity.START);
        root.addView(title, new LinearLayout.LayoutParams(-1, -2));

        TextView subtitle = new TextView(this);
        subtitle.setText("Personal library • " + SERVER_URL);
        subtitle.setTextSize(12);
        subtitle.setTextColor(0xff6b6761);
        root.addView(subtitle, new LinearLayout.LayoutParams(-1, -2));

        absStatus = meta("Audiobookshelf: checking…");
        root.addView(absStatus, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout absActions = new LinearLayout(this);
        absActions.setOrientation(LinearLayout.HORIZONTAL);
        Button absCheck = button("Check ABS");
        absCheck.setOnClickListener(v -> loadAudiobookshelfStatus());
        Button absScan = button("Scan ABS");
        absScan.setOnClickListener(v -> scanAudiobookshelf());
        absActions.addView(absCheck, new LinearLayout.LayoutParams(0, -2, 1));
        absActions.addView(absScan, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(absActions, new LinearLayout.LayoutParams(-1, -2));

        searchBox = new EditText(this);
        searchBox.setHint("Search audiobook title");
        searchBox.setSingleLine(true);
        searchBox.setInputType(InputType.TYPE_CLASS_TEXT);
        searchBox.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        searchBox.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                search();
                return true;
            }
            return false;
        });
        root.addView(searchBox, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        Button libraryButton = button("Library");
        libraryButton.setOnClickListener(v -> loadLibrary());
        Button searchButton = button("Search");
        searchButton.setOnClickListener(v -> search());
        actions.addView(libraryButton, new LinearLayout.LayoutParams(0, -2, 1));
        actions.addView(searchButton, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(actions, new LinearLayout.LayoutParams(-1, -2));

        status = new TextView(this);
        status.setTextColor(0xff4f4b45);
        status.setTextSize(14);
        root.addView(status, new LinearLayout.LayoutParams(-1, -2));

        spinner = new ProgressBar(this);
        spinner.setVisibility(View.GONE);
        root.addView(spinner, new LinearLayout.LayoutParams(-1, dp(8)));

        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        setContentView(root);
    }

    private Button button(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        return b;
    }

    private TextView cardTitle(String text) {
        TextView v = new TextView(this);
        v.setText(text);
        v.setTextSize(18);
        v.setTextColor(0xff111111);
        v.setPadding(0, dp(12), 0, dp(4));
        return v;
    }

    private TextView meta(String text) {
        TextView v = new TextView(this);
        v.setText(text == null ? "" : text);
        v.setTextSize(13);
        v.setTextColor(0xff6b6761);
        return v;
    }

    private void loadAudiobookshelfStatus() {
        getJson("/api/audiobookshelf/status", json -> runOnUiThread(() -> {
            boolean configured = json.optBoolean("configured");
            boolean reachable = json.optBoolean("reachable");
            boolean authorized = json.optBoolean("authorized");
            boolean libraryFound = json.optBoolean("library_found");
            JSONObject library = json.optJSONObject("library");
            if (configured && reachable && authorized && libraryFound) {
                String name = library == null ? "ready" : library.optString("name", "ready");
                absStatus.setText("Audiobookshelf: ready • " + name);
            } else if (!configured) {
                absStatus.setText("Audiobookshelf: not configured");
            } else {
                absStatus.setText("Audiobookshelf: needs attention");
            }
        }));
    }

    private void scanAudiobookshelf() {
        setBusy("Scanning Audiobookshelf…");
        postJson("/api/audiobookshelf/scan", "{\"force\":true}", json -> runOnUiThread(() -> {
            Toast.makeText(this, json.optString("message", "Scan started"), Toast.LENGTH_LONG).show();
            setStatus("Audiobookshelf scan started");
            loadAudiobookshelfStatus();
        }));
    }

    private void loadLibrary() {
        setBusy("Loading library…");
        getJson("/api/library", json -> runOnUiThread(() -> {
            content.removeAllViews();
            JSONArray books = json.optJSONArray("books");
            status.setText((books == null ? 0 : books.length()) + " books in library");
            if (books == null || books.length() == 0) {
                content.addView(meta("No books yet. Search and add one."));
            } else {
                for (int i = 0; i < books.length(); i++) addBookRow(books.optJSONObject(i));
            }
            setIdle();
        }));
    }

    private void search() {
        String query = searchBox.getText().toString().trim();
        if (query.isEmpty()) {
            Toast.makeText(this, "Type a title first", Toast.LENGTH_SHORT).show();
            return;
        }
        setBusy("Searching…");
        getJson("/api/search?q=" + Uri.encode(query), json -> runOnUiThread(() -> {
            content.removeAllViews();
            JSONArray books = json.optJSONArray("books");
            status.setText((books == null ? 0 : books.length()) + " search results");
            if (books == null || books.length() == 0) {
                content.addView(meta("No results."));
            } else {
                for (int i = 0; i < books.length(); i++) addSearchRow(books.optJSONObject(i));
            }
            setIdle();
        }));
    }

    private void addSearchRow(JSONObject book) {
        if (book == null) return;
        content.addView(cardTitle(book.optString("title", "Untitled")));
        content.addView(meta(joinMeta(
                book.optString("file_size", ""),
                book.optString("format", ""),
                book.optString("language", ""),
                book.optString("bitrate", ""),
                book.optString("post_date", "")
        )));
        Button add = button("Add / prepare on server");
        add.setOnClickListener(v -> addBook(book));
        content.addView(add);
    }

    private void addBook(JSONObject book) {
        setBusy("Adding to Real-Debrid…");
        postJson("/api/add", book.toString(), json -> runOnUiThread(() -> {
            int bookId = json.optInt("book_id", 0);
            Toast.makeText(this, json.optString("message", "Added"), Toast.LENGTH_LONG).show();
            if (bookId > 0) loadBook(bookId); else loadLibrary();
        }));
    }

    private void addBookRow(JSONObject book) {
        if (book == null) return;
        int id = book.optInt("id");
        String statusText = book.optString("status", "");
        int progress = book.optInt("progress");
        boolean readyForImport = "downloaded".equals(statusText) && progress >= 100;

        content.addView(cardTitle(book.optString("title", "Untitled")));
        content.addView(meta(joinMeta(
                statusText,
                progress + "%",
                book.optString("file_size", ""),
                book.optString("format", "")
        )));
        String error = book.optString("error", "");
        if (!error.isEmpty()) content.addView(meta("Error: " + error));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button open = button("Open");
        open.setOnClickListener(v -> loadBook(id));
        Button prepare = button("Refresh");
        prepare.setOnClickListener(v -> prepareBook(id));
        Button importAbs = button(readyForImport ? "Import ABS" : "Wait");
        importAbs.setEnabled(readyForImport);
        importAbs.setOnClickListener(v -> importToAudiobookshelf(id));
        row.addView(open, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(prepare, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(importAbs, new LinearLayout.LayoutParams(0, -2, 1));
        content.addView(row);
    }

    private void importToAudiobookshelf(int id) {
        setBusy("Importing to Audiobookshelf…");
        postJson("/api/books/" + id + "/import-to-audiobookshelf", "{}", json -> runOnUiThread(() -> {
            int count = json.optInt("count", 0);
            Toast.makeText(this, json.optString("message", "Imported") + " (" + count + " files)", Toast.LENGTH_LONG).show();
            status.setText("Imported to Audiobookshelf: " + count + " files");
            setIdle();
            loadAudiobookshelfStatus();
        }));
    }

    private void prepareBook(int id) {
        setBusy("Preparing book…");
        postJson("/api/books/" + id + "/prepare", "{}", json -> runOnUiThread(() -> {
            Toast.makeText(this, json.optString("message", "Prepared"), Toast.LENGTH_SHORT).show();
            loadBook(id);
        }));
    }

    private void loadBook(int id) {
        setBusy("Loading book…");
        getJson("/api/books/" + id, json -> runOnUiThread(() -> {
            content.removeAllViews();
            JSONObject book = json.optJSONObject("book");
            currentTitle = book == null ? "Audiobook" : book.optString("title", "Audiobook");
            status.setText(currentTitle);
            boolean readyForImport = false;
            if (book != null) {
                String statusText = book.optString("status", "");
                int progress = book.optInt("progress");
                readyForImport = "downloaded".equals(statusText) && progress >= 100;
                content.addView(meta(joinMeta(
                        statusText,
                        progress + "%",
                        book.optString("file_size", "")
                )));
                String error = book.optString("error", "");
                if (!error.isEmpty()) content.addView(meta("Error: " + error));
            }

            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            Button back = button("Library");
            back.setOnClickListener(v -> loadLibrary());
            Button prepare = button("Prepare / Refresh");
            prepare.setOnClickListener(v -> prepareBook(id));
            Button importAbs = button(readyForImport ? "Import ABS" : "Wait");
            importAbs.setEnabled(readyForImport);
            importAbs.setOnClickListener(v -> importToAudiobookshelf(id));
            actions.addView(back, new LinearLayout.LayoutParams(0, -2, 1));
            actions.addView(prepare, new LinearLayout.LayoutParams(0, -2, 1));
            actions.addView(importAbs, new LinearLayout.LayoutParams(0, -2, 1));
            content.addView(actions);

            JSONArray files = json.optJSONArray("files");
            if (files == null || files.length() == 0) {
                content.addView(meta("No playable files yet. Tap Prepare or try again later."));
            } else {
                for (int i = 0; i < files.length(); i++) addFileRow(files.optJSONObject(i));
            }
            setIdle();
        }));
    }

    private void addFileRow(JSONObject file) {
        if (file == null) return;
        String name = file.optString("filename", "audio");
        String streamUrl = absolute(file.optString("stream_url", ""));
        content.addView(cardTitle(name));
        content.addView(meta(file.optString("note", "") + "  " + bytesLabel(file.optLong("bytes"))));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button play = button("Play");
        play.setOnClickListener(v -> play(streamUrl, name));
        Button pause = button("Pause");
        pause.setOnClickListener(v -> pause());
        Button download = button("Download");
        download.setOnClickListener(v -> download(streamUrl, name));
        row.addView(play, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(pause, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(download, new LinearLayout.LayoutParams(0, -2, 1));
        content.addView(row);
    }

    private void play(String url, String name) {
        if (url.isEmpty()) return;
        setBusy("Buffering " + name + "…");
        try {
            stopPlayer();
            player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .build());
            player.setDataSource(url);
            player.setOnPreparedListener(mp -> {
                mp.start();
                runOnUiThread(() -> setStatus("Playing: " + name));
            });
            player.setOnErrorListener((mp, what, extra) -> {
                runOnUiThread(() -> setStatus("Playback failed"));
                return true;
            });
            player.prepareAsync();
        } catch (Exception e) {
            setStatus("Playback failed: " + e.getMessage());
        }
    }

    private void pause() {
        if (player != null && player.isPlaying()) {
            player.pause();
            setStatus("Paused");
        }
    }

    private void download(String url, String name) {
        if (url.isEmpty()) return;
        DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
        request.setTitle(name);
        request.setDescription(currentTitle);
        request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
        request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Audiobooks/" + safeName(name));
        DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
        manager.enqueue(request);
        Toast.makeText(this, "Download started", Toast.LENGTH_SHORT).show();
    }

    private void getJson(String path, JsonHandler handler) {
        request("GET", path, null, handler);
    }

    private void postJson(String path, String body, JsonHandler handler) {
        request("POST", path, body, handler);
    }

    private void request(String method, String path, String body, JsonHandler handler) {
        executor.execute(() -> {
            HttpURLConnection conn = null;
            try {
                URL url = new URL(absolute(path));
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod(method);
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(120000);
                conn.setRequestProperty("Accept", "application/json");
                if (body != null) {
                    conn.setDoOutput(true);
                    conn.setRequestProperty("Content-Type", "application/json");
                    try (OutputStream os = conn.getOutputStream()) {
                        os.write(body.getBytes(StandardCharsets.UTF_8));
                    }
                }
                int code = conn.getResponseCode();
                InputStream stream = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
                String text = readAll(stream);
                if (code >= 400) throw new RuntimeException(errorMessage(text, code));
                handler.handle(new JSONObject(text));
            } catch (Exception e) {
                runOnUiThread(() -> {
                    setIdle();
                    status.setText("Error: " + e.getMessage());
                    Toast.makeText(this, "Request failed", Toast.LENGTH_LONG).show();
                });
            } finally {
                if (conn != null) conn.disconnect();
            }
        });
    }

    private String errorMessage(String text, int code) {
        if (text == null || text.isEmpty()) return "HTTP " + code;
        try {
            JSONObject json = new JSONObject(text);
            String message = json.optString("message", "");
            if (!message.isEmpty()) return message;
        } catch (Exception ignored) {}
        return text;
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) return "";
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) sb.append(line);
        return sb.toString();
    }

    private String absolute(String path) {
        if (path == null || path.isEmpty()) return "";
        if (path.startsWith("http://") || path.startsWith("https://")) return path;
        if (!path.startsWith("/")) path = "/" + path;
        return SERVER_URL + path;
    }

    private void setBusy(String text) {
        runOnUiThread(() -> {
            status.setText(text);
            spinner.setVisibility(View.VISIBLE);
        });
    }

    private void setIdle() {
        spinner.setVisibility(View.GONE);
    }

    private void setStatus(String text) {
        status.setText(text);
        spinner.setVisibility(View.GONE);
    }

    private String joinMeta(String... parts) {
        StringBuilder sb = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.trim().isEmpty()) continue;
            if (sb.length() > 0) sb.append(" • ");
            sb.append(part.trim());
        }
        return sb.toString();
    }

    private String safeName(String name) {
        return name.replaceAll("[^a-zA-Z0-9._ -]", "_");
    }

    private String bytesLabel(long bytes) {
        if (bytes <= 0) return "";
        double mb = bytes / 1024.0 / 1024.0;
        if (mb > 1024) return String.format("%.1f GB", mb / 1024.0);
        return String.format("%.1f MB", mb);
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void stopPlayer() {
        if (player != null) {
            try { player.stop(); } catch (Exception ignored) {}
            player.release();
            player = null;
        }
    }

    @Override
    protected void onDestroy() {
        stopPlayer();
        executor.shutdownNow();
        super.onDestroy();
    }

    interface JsonHandler {
        void handle(JSONObject json) throws Exception;
    }
}
