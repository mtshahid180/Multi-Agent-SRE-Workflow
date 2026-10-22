// Tabs on the home page
document.querySelectorAll(".seg").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll(".seg").forEach(function (b) { b.classList.remove("active"); });
    document.querySelectorAll(".tab").forEach(function (t) { t.classList.add("hidden"); });
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.remove("hidden");
  });
});

// Fill the custom form with the sample bug
var fill = document.getElementById("fill-sample");
if (fill) {
  fill.addEventListener("click", function () {
    document.getElementById("f-title").value = "Report job crashes with ZeroDivisionError";
    document.getElementById("f-logs").value =
      "Traceback (most recent call last):\n" +
      '  File "/app/report.py", line 18, in build_report\n' +
      "    avg = average(order_totals)\n" +
      '  File "/app/calculator/stats.py", line 6, in average\n' +
      "    return total / len(values)\n" +
      "ZeroDivisionError: division by zero";
    document.getElementById("f-tests").value =
      "FAILED tests/test_stats.py::test_average_of_empty_list_is_zero - ZeroDivisionError";
  });
}

// Copy buttons
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    navigator.clipboard.writeText(btn.dataset.copy).then(function () {
      var old = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(function () { btn.textContent = old; }, 1500);
    });
  });
});

// Show event times in the viewer's own time zone
document.querySelectorAll("time[data-ts]").forEach(function (el) {
  var d = new Date(el.dataset.ts);
  if (!isNaN(d)) { el.textContent = d.toLocaleTimeString(); }
});

// Live updates: reload the page when the incident changes
var body = document.body;
if (body.dataset.poll === "1") {
  var last = body.dataset.updated;
  setInterval(function () {
    fetch("/incident/" + body.dataset.id + "/state")
      .then(function (r) { return r.json(); })
      .then(function (s) { if (s.updated_at && s.updated_at !== last) { location.reload(); } })
      .catch(function () {});
  }, 3000);
}
