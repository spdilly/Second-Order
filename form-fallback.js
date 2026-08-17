/* Second Order — form submission.
 *
 * Every form on the site posts to one endpoint, a Google Apps Script web app
 * that appends each submission to a spreadsheet. Until that endpoint is set,
 * the action attribute carries the SHEET_ENDPOINT placeholder and this script
 * composes a prefilled email to the firm address instead, so the site accepts
 * enquiries either way. When the endpoint is live, this script submits in the
 * background and confirms inline. If the background submit fails, it falls
 * back to the prefilled email.
 */
(function () {
  'use strict';

  var forms = document.querySelectorAll('form.intake');
  if (!forms.length) return;

  function mailtoFallback(form) {
    var to = form.getAttribute('data-fallback-to') || 'sean@2oadvisory.com';
    var subjectField = form.querySelector('input[name="_subject"]');
    var subject = subjectField ? subjectField.value : 'Enquiry from 2oadvisory.com';

    var lines = [];
    Array.prototype.forEach.call(form.querySelectorAll('input, textarea'), function (el) {
      if (!el.name || el.name.charAt(0) === '_' || el.name === 'request_type') return;
      if (!el.value.trim()) return;
      var label = el.id ? form.querySelector('label[for="' + el.id + '"]') : null;
      var key = label ? label.textContent.trim() : el.name.toUpperCase();
      lines.push(key + '\n' + el.value.trim());
    });

    if (!lines.length) {
      var first = form.querySelector('input[required], textarea[required]');
      if (first) first.focus();
      return;
    }

    window.location.href =
      'mailto:' + to +
      '?subject=' + encodeURIComponent(subject) +
      '&body=' + encodeURIComponent(lines.join('\n\n'));
  }

  function confirm(form) {
    var message = form.getAttribute('data-success') || 'Received. A reply comes back by email.';
    var note = document.createElement('p');
    note.className = 'hint';
    note.setAttribute('role', 'status');
    note.textContent = message;
    form.parentNode.replaceChild(note, form);
  }

  Array.prototype.forEach.call(forms, function (form) {
    var action = form.getAttribute('action') || '';
    var live = action.indexOf('https://') === 0;

    form.addEventListener('submit', function (event) {
      event.preventDefault();

      var gotcha = form.querySelector('input[name="_gotcha"]');
      if (gotcha && gotcha.value) { confirm(form); return; }

      if (!live) { mailtoFallback(form); return; }

      var params = new URLSearchParams();
      Array.prototype.forEach.call(form.querySelectorAll('input, textarea'), function (el) {
        if (!el.name || el.name === '_gotcha') return;
        params.append(el.name, el.value);
      });
      params.append('page', window.location.pathname);

      fetch(action, { method: 'POST', mode: 'no-cors', body: params })
        .then(function () { confirm(form); })
        .catch(function () { mailtoFallback(form); });
    });
  });
})();
