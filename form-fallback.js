/* Second Order — form fallback.
 *
 * Every form on the site posts to one Formspree endpoint. Until that endpoint
 * exists, the action attribute still carries the FORMSPREE_ID placeholder and a
 * real submission would 404. This script detects the placeholder and, in that
 * case only, composes a prefilled email to the firm address instead. The site
 * can therefore launch and accept enquiries before the endpoint is created.
 *
 * When the placeholder is replaced with a real Formspree id, this script stops
 * intercepting and the forms post normally. Nothing needs to be removed.
 */
(function () {
  'use strict';

  var forms = document.querySelectorAll('form[action*="FORMSPREE_ID"]');
  if (!forms.length) return;

  Array.prototype.forEach.call(forms, function (form) {
    var notice = form.querySelector('[data-fallback-notice]');
    if (notice) notice.hidden = false;

    form.addEventListener('submit', function (event) {
      event.preventDefault();

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
    });
  });
})();
