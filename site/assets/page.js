(function () {
  var ko = document.documentElement.lang === "ko";
  var COPY = ko ? "복사" : "Copy";
  var COPIED = ko ? "복사됨" : "Copied";

  // The download buttons point at the releases page; swap in the newest .mcpb when GitHub answers.
  fetch("https://api.github.com/repos/Dennis5882/midas-nx-mcp/releases/latest")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (release) {
      if (!release) return;
      var asset = (release.assets || []).find(function (a) { return /\.mcpb$/.test(a.name); });
      if (asset) {
        document.querySelectorAll("[data-download]").forEach(function (a) {
          a.href = asset.browser_download_url;
        });
      }
      var date = (release.published_at || "").slice(0, 10);
      document.getElementById("version").textContent = release.tag_name + (date ? " · " + date : "");
    })
    .catch(function () {});

  document.querySelectorAll(".tabs button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".tabs button").forEach(function (b) {
        b.setAttribute("aria-selected", String(b === btn));
        document.getElementById(b.dataset.tab).hidden = b !== btn;
      });
    });
  });

  function copyButton(button, text) {
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(text()).then(function () {
        button.textContent = COPIED;
        setTimeout(function () { button.textContent = COPY; }, 1500);
      });
    });
  }
  document.querySelectorAll(".code").forEach(function (block) {
    var button = document.createElement("button");
    button.className = "copy";
    button.textContent = COPY;
    copyButton(button, function () { return block.querySelector("code").textContent; });
    block.appendChild(button);
  });
  document.querySelectorAll(".copy-inline").forEach(function (button) {
    copyButton(button, function () { return document.getElementById(button.dataset.copy).textContent; });
  });
})();
