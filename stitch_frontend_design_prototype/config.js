/* Local desk calls the same origin. The Vercel site calls the Render API. */
(function () {
  var host = location.hostname;
  var local = host === "127.0.0.1" || host === "localhost";
  window.DESK_API = local ? "" : "https://multiagentsystems.onrender.com";
})();
