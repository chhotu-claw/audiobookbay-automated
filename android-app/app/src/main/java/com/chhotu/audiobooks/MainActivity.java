package com.chhotu.audiobooks;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
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

    private static final int BG = 0xfff7f3ee;
    private static final int SURFACE = 0xffffffff;
    private static final int SURFACE_SOFT = 0xfffbfaf8;
    private static final int COVER_BG = 0xffebe6de;
    private static final int INK = 0xff151515;
    private static final int INK_SOFT = 0xff3e3e3e;
    private static final int MUTED = 0xff77736d;
    private static final int PLACEHOLDER = 0xff9a958e;
    private static final int LINE = 0xffded8cf;
    private static final int LINE_STRONG = 0xffcfc8bd;
    private static final int DANGER = 0xff9f1d1d;
    private static final int DANGER_BG = 0xfffff4f2;

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
        root.setBackgroundColor(BG);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(0, 0, 0, dp(10));

        TextView mark = new TextView(this);
        mark.setText("AB");
        mark.setGravity(Gravity.CENTER);
        mark.setTextColor(INK);
        mark.setTextSize(12);
        mark.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        mark.setBackground(rounded(SURFACE, INK, 1, 11));
        LinearLayout.LayoutParams markLp = new LinearLayout.LayoutParams(dp(38), dp(38));
        markLp.setMargins(0, 0, dp(10), 0);
        header.addView(mark, markLp);

        LinearLayout brand = new LinearLayout(this);
        brand.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("Chhotu Audiobooks");
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setTextColor(INK);
        title.setIncludeFontPadding(false);
        brand.addView(title, new LinearLayout.LayoutParams(-1, -2));

        TextView subtitle = meta("Quiet listening library • " + SERVER_URL);
        brand.addView(subtitle, new LinearLayout.LayoutParams(-1, -2));
        header.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(header, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout absCard = sectionCard();
        absStatus = meta("Audiobookshelf: checking…");
        absCard.addView(label("Audiobookshelf"));
        absCard.addView(absStatus, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout absActions = new LinearLayout(this);
        absActions.setOrientation(LinearLayout.HORIZONTAL);
        Button absCheck = secondaryButton("Check ABS");
        absCheck.setOnClickListener(v -> loadAudiobookshelfStatus());
        Button absScan = secondaryButton("Scan ABS");
        absScan.setOnClickListener(v -> scanAudiobookshelf());
        absActions.addView(absCheck, rowButtonLp(true));
        absActions.addView(absScan, rowButtonLp(false));
        absCard.addView(absActions, topMarginLp(-1, -2, 10));
        root.addView(absCard, bottomMarginLp(-1, -2, 12));

        searchBox = new EditText(this);
        searchBox.setHint("Search audiobook title");
        searchBox.setHintTextColor(PLACEHOLDER);
        searchBox.setTextColor(INK);
        searchBox.setTextSize(15);
        searchBox.setSingleLine(true);
        searchBox.setInputType(InputType.TYPE_CLASS_TEXT);
        searchBox.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        searchBox.setPadding(dp(16), 0, dp(16), 0);
        searchBox.setMinHeight(dp(46));
        searchBox.setBackground(rounded(SURFACE_SOFT, LINE_STRONG, 1, 23));
        searchBox.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                search();
                return true;
            }
            return false;
        });
        root.addView(searchBox, bottomMarginLp(-1, dp(46), 10));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        Button libraryButton = secondaryButton("Library");
        libraryButton.setOnClickListener(v -> loadLibrary());
        Button searchButton = primaryButton("Search");
        searchButton.setOnClickListener(v -> search());
        actions.addView(libraryButton, rowButtonLp(true));
        actions.addView(searchButton, rowButtonLp(false));
        root.addView(actions, bottomMarginLp(-1, -2, 12));

        status = meta("Ready");
        status.setTextColor(INK_SOFT);
        status.setBackground(rounded(SURFACE_SOFT, LINE, 1, 14));
        status.setPadding(dp(12), dp(9), dp(12), dp(9));
        root.addView(status, bottomMarginLp(-1, -2, 8));

        spinner = new ProgressBar(this);
        spinner.setVisibility(View.GONE);
        LinearLayout.LayoutParams spinnerLp = bottomMarginLp(-1, -2, 8);
        spinnerLp.gravity = Gravity.CENTER_HORIZONTAL;
        root.addView(spinner, spinnerLp);

        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        setContentView(root);
    }

    private Button primaryButton(String text) {
        return styledButton(text, true);
    }

    private Button secondaryButton(String text) {
        return styledButton(text, false);
    }

    private Button button(String text) {
        return secondaryButton(text);
    }

    private Button styledButton(String text, boolean primary) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(14);
        b.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        b.setMinHeight(dp(42));
        b.setPadding(dp(16), 0, dp(16), 0);
        b.setTextColor(primary ? SURFACE : INK);
        b.setBackground(rounded(primary ? INK : SURFACE, primary ? INK : LINE_STRONG, 1, 21));
        return b;
    }

    private TextView cardTitle(String text) {
        TextView v = new TextView(this);
        v.setText(text);
        v.setTextSize(17);
        v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        v.setTextColor(INK);
        v.setIncludeFontPadding(false);
        v.setPadding(0, 0, 0, dp(7));
        return v;
    }

    private TextView meta(String text) {
        TextView v = new TextView(this);
        v.setText(text == null ? "" : text);
        v.setTextSize(13);
        v.setTextColor(MUTED);
        v.setIncludeFontPadding(false);
        return v;
    }

    private TextView label(String text) {
        TextView v = meta(text == null ? "" : text.toUpperCase());
        v.setTextSize(11);
        v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        v.setPadding(0, 0, 0, dp(5));
        return v;
    }

    private LinearLayout sectionCard() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(13), dp(14), dp(13));
        card.setBackground(rounded(SURFACE, LINE, 1, 18));
        return card;
    }

    private TextView coverPlaceholder() {
        TextView cover = new TextView(this);
        cover.setText("AB");
        cover.setGravity(Gravity.CENTER);
        cover.setTextSize(13);
        cover.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        cover.setTextColor(MUTED);
        cover.setBackground(rounded(COVER_BG, LINE, 1, 10));
        return cover;
    }

    private TextView chip(String text) {
        TextView v = meta(text);
        v.setTextSize(12);
        v.setPadding(dp(9), dp(5), dp(9), dp(5));
        v.setBackground(rounded(SURFACE_SOFT, LINE, 1, 13));
        return v;
    }

    private LinearLayout chips(String... parts) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        int shown = 0;
        for (String part : parts) {
            if (part == null || part.trim().isEmpty()) continue;
            TextView chip = chip(part.trim());
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
            lp.setMargins(0, 0, dp(6), dp(6));
            row.addView(chip, lp);
            shown++;
            if (shown >= 3) break;
        }
        return row;
    }

    private TextView notice(String text, boolean error) {
        TextView v = meta(text);
        v.setTextColor(error ? DANGER : MUTED);
        v.setPadding(dp(13), dp(12), dp(13), dp(12));
        v.setBackground(rounded(error ? DANGER_BG : SURFACE, error ? DANGER : LINE, 1, 14));
        return v;
    }

    private void addCard(LinearLayout card) {
        content.addView(card, bottomMarginLp(-1, -2, 12));
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
                absStatus.setText("Ready • " + name);
            } else if (!configured) {
                absStatus.setText("Not configured");
            } else {
                absStatus.setText("Needs attention");
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
                content.addView(notice("No books yet. Search and add one.", false), bottomMarginLp(-1, -2, 12));
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
                content.addView(notice("No results.", false), bottomMarginLp(-1, -2, 12));
            } else {
                for (int i = 0; i < books.length(); i++) addSearchRow(books.optJSONObject(i));
            }
            setIdle();
        }));
    }

    private void addSearchRow(JSONObject book) {
        if (book == null) return;
        LinearLayout card = bookCardBase();
        LinearLayout body = (LinearLayout) card.getChildAt(1);
        body.addView(cardTitle(book.optString("title", "Untitled")));
        body.addView(chips(
                book.optString("file_size", ""),
                book.optString("format", ""),
                book.optString("language", "")
        ));
        Button add = primaryButton("Add / prepare on server");
        add.setOnClickListener(v -> addBook(book));
        body.addView(add, topMarginLp(-1, -2, 8));
        addCard(card);
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

        LinearLayout card = bookCardBase();
        LinearLayout body = (LinearLayout) card.getChildAt(1);
        body.addView(cardTitle(book.optString("title", "Untitled")));
        body.addView(chips(
                statusText,
                progress + "%",
                book.optString("file_size", "")
        ));
        String error = book.optString("error", "");
        if (!error.isEmpty()) body.addView(notice("Error: " + error, true), topMarginLp(-1, -2, 8));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button open = primaryButton("Open");
        open.setOnClickListener(v -> loadBook(id));
        Button prepare = secondaryButton("Refresh");
        prepare.setOnClickListener(v -> prepareBook(id));
        Button importAbs = readyForImport ? primaryButton("Import ABS") : secondaryButton("Wait");
        importAbs.setEnabled(readyForImport);
        importAbs.setOnClickListener(v -> importToAudiobookshelf(id));
        row.addView(open, rowButtonLp(true));
        row.addView(prepare, rowButtonLp(true));
        row.addView(importAbs, rowButtonLp(false));
        body.addView(row, topMarginLp(-1, -2, 8));
        addCard(card);
    }

    private LinearLayout bookCardBase() {
        LinearLayout card = sectionCard();
        card.setOrientation(LinearLayout.HORIZONTAL);
        TextView cover = coverPlaceholder();
        LinearLayout.LayoutParams coverLp = new LinearLayout.LayoutParams(dp(66), dp(99));
        coverLp.setMargins(0, 0, dp(13), 0);
        card.addView(cover, coverLp);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        card.addView(body, new LinearLayout.LayoutParams(0, -2, 1));
        return card;
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

            LinearLayout bookCard = sectionCard();
            bookCard.addView(cardTitle(currentTitle));
            if (book != null) {
                String statusText = book.optString("status", "");
                int progress = book.optInt("progress");
                readyForImport = "downloaded".equals(statusText) && progress >= 100;
                bookCard.addView(chips(
                        statusText,
                        progress + "%",
                        book.optString("file_size", "")
                ));
                String error = book.optString("error", "");
                if (!error.isEmpty()) bookCard.addView(notice("Error: " + error, true), topMarginLp(-1, -2, 8));
            }

            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            Button back = secondaryButton("Library");
            back.setOnClickListener(v -> loadLibrary());
            Button prepare = secondaryButton("Prepare");
            prepare.setOnClickListener(v -> prepareBook(id));
            Button importAbs = readyForImport ? primaryButton("Import ABS") : secondaryButton("Wait");
            importAbs.setEnabled(readyForImport);
            importAbs.setOnClickListener(v -> importToAudiobookshelf(id));
            actions.addView(back, rowButtonLp(true));
            actions.addView(prepare, rowButtonLp(true));
            actions.addView(importAbs, rowButtonLp(false));
            bookCard.addView(actions, topMarginLp(-1, -2, 10));
            addCard(bookCard);

            JSONArray files = json.optJSONArray("files");
            if (files == null || files.length() == 0) {
                content.addView(notice("No playable files yet. Tap Prepare or try again later.", false), bottomMarginLp(-1, -2, 12));
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
        LinearLayout card = sectionCard();
        card.addView(cardTitle(name));
        card.addView(chips(file.optString("note", ""), bytesLabel(file.optLong("bytes"))));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button play = primaryButton("Play");
        play.setOnClickListener(v -> play(streamUrl, name));
        Button pause = secondaryButton("Pause");
        pause.setOnClickListener(v -> pause());
        Button download = secondaryButton("Download");
        download.setOnClickListener(v -> download(streamUrl, name));
        row.addView(play, rowButtonLp(true));
        row.addView(pause, rowButtonLp(true));
        row.addView(download, rowButtonLp(false));
        card.addView(row, topMarginLp(-1, -2, 9));
        addCard(card);
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

    private String safeName(String name) {
        return name.replaceAll("[^a-zA-Z0-9._ -]", "_");
    }

    private String bytesLabel(long bytes) {
        if (bytes <= 0) return "";
        double mb = bytes / 1024.0 / 1024.0;
        if (mb > 1024) return String.format("%.1f GB", mb / 1024.0);
        return String.format("%.1f MB", mb);
    }

    private GradientDrawable rounded(int color, int strokeColor, int strokeDp, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setShape(GradientDrawable.RECTANGLE);
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) drawable.setStroke(dp(strokeDp), strokeColor);
        return drawable;
    }

    private LinearLayout.LayoutParams rowButtonLp(boolean marginRight) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(42), 1);
        if (marginRight) lp.setMargins(0, 0, dp(8), 0);
        return lp;
    }

    private LinearLayout.LayoutParams bottomMarginLp(int width, int height, int bottomDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(width, height);
        lp.setMargins(0, 0, 0, dp(bottomDp));
        return lp;
    }

    private LinearLayout.LayoutParams topMarginLp(int width, int height, int topDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(width, height);
        lp.setMargins(0, dp(topDp), 0, 0);
        return lp;
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
