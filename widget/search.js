(function () {
  // Replace with the value from: terraform output api_gateway_url
  var API_URL = "REPLACE_WITH_API_GATEWAY_URL";

  var container = document.getElementById("jk-blog-search");
  if (!container) return;

  container.innerHTML =
    '<form class="jk-search-widget__form" id="jk-bs-form">' +
      '<input class="jk-search-widget__input" id="jk-bs-input" type="text"' +
        ' placeholder="Ask a question about my blog..." autocomplete="off" />' +
      '<button class="jk-search-widget__btn" id="jk-bs-btn" type="submit">Ask</button>' +
    '</form>' +
    '<div id="jk-bs-result"></div>';

  var form   = document.getElementById("jk-bs-form");
  var input  = document.getElementById("jk-bs-input");
  var btn    = document.getElementById("jk-bs-btn");
  var result = document.getElementById("jk-bs-result");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var question = input.value.trim();
    if (!question) return;

    btn.disabled = true;
    result.innerHTML = '<p class="jk-search-widget__spinner">Thinking…</p>';

    fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var sourcesHtml = "";
        if (data.sources && data.sources.length) {
          sourcesHtml =
            '<div class="jk-search-widget__sources">' +
              '<div class="jk-search-widget__sources-label">Sources</div>' +
              data.sources
                .map(function (s) {
                  return (
                    '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">' +
                    esc(s.title) + "</a>"
                  );
                })
                .join("") +
            "</div>";
        }
        result.innerHTML =
          '<div class="jk-search-widget__result">' +
            '<p class="jk-search-widget__answer">' + esc(data.answer) + "</p>" +
            sourcesHtml +
          "</div>";
      })
      .catch(function () {
        result.innerHTML =
          '<p class="jk-search-widget__error">Something went wrong. Please try again.</p>';
      })
      .finally(function () {
        btn.disabled = false;
      });
  });

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
