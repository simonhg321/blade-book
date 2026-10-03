// Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
// blade-book shared nav (plan 13): one /auth/me fetch → "sign in" becomes
// "my register", the admin link shows for admins, and #signout works.
// Included at the end of every page; never throws; textContent only.
(function () {
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn();
  }
  ready(function () {
    var out = document.getElementById('signout');
    if (out) {
      out.addEventListener('click', function () {
        fetch('/blade-book/api/auth/signout', { method: 'POST', credentials: 'same-origin', cache: 'no-store' })
          .then(function () { location.href = '/blade-book/'; }, function () { location.href = '/blade-book/'; })
          .catch(function () {});
      });
    }
    fetch('/blade-book/api/auth/me', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        if (!me) return;
        Array.prototype.forEach.call(document.querySelectorAll('a.bb-auth'), function (a) { a.textContent = 'my register'; });
        if (me.is_admin) {
          Array.prototype.forEach.call(document.querySelectorAll('#adminlink'), function (a) { a.hidden = false; });
        }
      })
      .catch(function () {});
  });
})();
