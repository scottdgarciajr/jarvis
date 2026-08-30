(function () {
  'use strict';
  var token = sessionStorage.getItem('jarvisToken');
  function pair(value) { if (!value) return; sessionStorage.setItem('jarvisToken', value); window.location.reload(); }
  if (!token) {
    try { var request = new XMLHttpRequest(); request.open('GET', '/api/local-pair', false); request.send(null); if (request.status === 200) pair(JSON.parse(request.responseText).token); } catch (ignore) {}
  }
  window.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('pair-form'), input = document.getElementById('pair-token');
    if (form) form.onsubmit = function (event) { event.preventDefault(); pair(input.value); };
  });
}());
