document.addEventListener("DOMContentLoaded", function () {
  // Initialize filtering if results are present
  if (document.querySelectorAll(".result-row").length > 0) {
    initializeFilters();
    document
      .getElementById("filter-button")
      .addEventListener("click", applyFilters);
    document
      .getElementById("clear-button")
      .addEventListener("click", clearFilters);
  }
});

let datePicker;
let fileSizeSlider;

function initializeFilters() {
    populateSelectFilters();
    initializeFileSizeSlider();
    initializeDateRangePicker();
}

// --- Helper Functions ---
function parseFileSizeToMB(sizeString) {
    if (!sizeString || sizeString.trim().toLowerCase() === 'n/a') return null;
    const parts = sizeString.trim().split(/\s+/);
    if (parts.length < 2) return null;
    const size = parseFloat(parts[0]);
    const unit = parts[1].toUpperCase();
    if (isNaN(size)) return null;
    if (unit.startsWith("TB")) return size * 1024 * 1024;
    if (unit.startsWith("GB")) return size * 1024;
    return size; // Assume MB
}

function formatFileSize(mb) {
    if (mb === null || isNaN(mb)) return "N/A";
    if (mb >= 1024 * 1024) {
        return (mb / (1024 * 1024)).toFixed(2) + " TB";
    }
    if (mb >= 1024) {
        return (mb / 1024).toFixed(2) + " GB";
    }
    return mb.toFixed(2) + " MB";
}


// --- Filtering Functions ---

function initializeDateRangePicker() {
    const allDates = Array.from(document.querySelectorAll('.result-row'))
        .map(row => {
            const dateStr = row.dataset.postDate;
            if (!dateStr || dateStr === 'N/A') return null;
            // Standardize the date format for reliable parsing
            const formattedStr = dateStr.replace(/(\d{1,2})\s(\w{3})\s(\d{4})/, '$2 $1, $3');
            const date = new Date(formattedStr);
            return isNaN(date) ? null : date;
        })
        .filter(date => date !== null);

    let options = {
        mode: "range",
        dateFormat: "Y-m-d"
    };

    if (allDates.length > 0) {
        const minDate = new Date(Math.min.apply(null, allDates));
        const maxDate = new Date(Math.max.apply(null, allDates));
        options.minDate = minDate;
        options.maxDate = maxDate;
    }

    datePicker = flatpickr("#date-range-filter", options);
}


function initializeFileSizeSlider() {
    const sliderElement = document.getElementById('file-size-slider');
    const allSizes = Array.from(document.querySelectorAll('.result-row'))
        .map(row => parseFileSizeToMB(row.dataset.fileSize))
        .filter(size => size !== null);

    if (allSizes.length < 2) {
        // Not enough data for a range slider, hide it
        document.querySelector('.file-size-filter-wrapper').style.display = 'none';
        return;
    }

    const minSize = Math.min(...allSizes);
    const maxSize = Math.max(...allSizes);

    // formatter for the tooltips
    const formatter = {
      to: function(value) {
        return formatFileSize(value);
      },
      from: function(value) {
        // This is needed for the slider to read its own formatted values
        return Number(parseFileSizeToMB(value));
      }
    };

    fileSizeSlider = noUiSlider.create(sliderElement, {
        start: [minSize, maxSize],
        connect: true,
        tooltips: [formatter, formatter], // Use the formatter for both tooltips
        range: {
            'min': minSize,
            'max': maxSize
        }
    });
}

function populateSelectFilters() {
  const languages = new Set();
  const bitrates = new Set();
  const formats = new Set();

  document.querySelectorAll(".result-row").forEach((row) => {
    languages.add(row.dataset.language);
    bitrates.add(row.dataset.bitrate);
    formats.add(row.dataset.format);
  });

  const languageFilter = document.getElementById("language-filter");
  languages.forEach((lang) => {
    if (lang && lang !== "N/A") {
      const option = document.createElement("option");
      option.value = lang;
      option.textContent = lang;
      languageFilter.appendChild(option);
    }
  });

  const bitrateFilter = document.getElementById("bitrate-filter");
  bitrates.forEach((rate) => {
    if (rate && rate !== "N/A") {
      const option = document.createElement("option");
      option.value = rate;
      option.textContent = rate;
      bitrateFilter.appendChild(option);
    }
  });

  const formatFilter = document.getElementById("format-filter");
  formats.forEach((format) => {
    if (format && format !== "N/A") {
      const option = document.createElement("option");
      option.value = format;
      option.textContent = format;
      formatFilter.appendChild(option);
    }
  });
}

function applyFilters() {
  const language = document.getElementById("language-filter").value;
  const bitrate = document.getElementById("bitrate-filter").value;
  const format = document.getElementById("format-filter").value;
  const selectedDates = datePicker.selectedDates;
  const sizeRange = fileSizeSlider ? fileSizeSlider.get().map(parseFloat) : null;


  document.querySelectorAll(".result-row").forEach((row) => {
    let visible = true;

    if (language && row.dataset.language !== language) visible = false;
    if (bitrate && row.dataset.bitrate !== bitrate) visible = false;
    if (format && row.dataset.format !== format) visible = false;
    
    // File size range filtering
    if (sizeRange) {
        const rowSizeMB = parseFileSizeToMB(row.dataset.fileSize);
        if (rowSizeMB !== null) {
            if (rowSizeMB < sizeRange[0] || rowSizeMB > sizeRange[1]) {
                visible = false;
            }
        }
    }

    // Date range filtering
    if (selectedDates.length === 2) {
        const rowDateStr = row.dataset.postDate;
        if (!rowDateStr || rowDateStr === 'N/A') {
            visible = false; // Hide items with no date if a date filter is active
        } else {
            try {
                const startDate = selectedDates[0];
                const endDate = selectedDates[1];
                // Standardize the date format from the HTML before parsing
                const formattedStr = rowDateStr.replace(/(\d{1,2})\s(\w{3})\s(\d{4})/, '$2 $1, $3');
                const rowDate = new Date(formattedStr);

                // Set time to 0 to compare dates only
                rowDate.setHours(0, 0, 0, 0);

                if (rowDate < startDate || rowDate > endDate) {
                    visible = false;
                }
            } catch (e) {
                console.error("Invalid date format", e);
                visible = false;
            }
        }
    }

    row.style.display = visible ? "" : "none";
  });
}

function clearFilters() {
  document.getElementById("language-filter").value = "";
  document.getElementById("bitrate-filter").value = "";
  document.getElementById("format-filter").value = "";
  if (datePicker) datePicker.clear();
  if (fileSizeSlider) fileSizeSlider.reset();
  
  document.querySelectorAll(".result-row").forEach((row) => {
    row.style.display = "";
  });
}

// --- Search Interaction Functions ---

function showLoadingSpinner() {
  const buttonSpinner = document.getElementById("button-spinner");
  const loadingSpinner = document.getElementById("loading-spinner");
  if (buttonSpinner) buttonSpinner.style.display = "inline-flex";
  if (loadingSpinner) loadingSpinner.style.display = "flex";
  setTimeout(showScrollingMessages, 5000);
}

function hideLoadingSpinner() {
  const buttonSpinner = document.getElementById("button-spinner");
  const loadingSpinner = document.getElementById("loading-spinner");
  if (buttonSpinner) buttonSpinner.style.display = "none";
  if (loadingSpinner) loadingSpinner.style.display = "none";
  hideScrollingMessages();
}

const messages = [
  "Still searching. AudioBookBay can take a moment.",
  "Checking the catalogue and keeping this page ready.",
  "Thanks for waiting — long searches sometimes need a little time.",
  "Gathering matching audiobook posts.",
  "Looking through available listings now.",
  "Search is still in progress.",
  "Almost there. Results will appear as soon as the search finishes.",
];
let messageIndex = 0;
let intervalId = null;

function showScrollingMessages() {
  const messageScroller = document.getElementById("message-scroller");
  const scrollingMessage = document.getElementById("scrolling-message");
  if(!scrollingMessage) return;
  const shuffledMessages = messages.sort(() => Math.random() - 0.5);
  messageScroller.style.display = "block";
  scrollingMessage.textContent = shuffledMessages[messageIndex];
  intervalId = setInterval(() => {
    messageIndex = (messageIndex + 1) % messages.length;
    scrollingMessage.textContent = shuffledMessages[messageIndex];
  }, 5000);
}

function hideScrollingMessages() {
  const messageScroller = document.getElementById("message-scroller");
  if (intervalId) {
    clearInterval(intervalId);
    intervalId = null;
  }
  if(messageScroller) messageScroller.style.display = "none";
}

function addToRealDebrid(book) {
  fetch("/api/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(book),
  })
    .then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Failed to add book");
      return data;
    })
    .then((data) => {
      alert(data.message);
      if (data.library_url) window.location.href = data.library_url;
    })
    .catch((error) => alert(error.message))
    .finally(hideLoadingSpinner);
}
