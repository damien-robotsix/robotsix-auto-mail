Honour ``X-Forwarded-Host`` and ``Forwarded: host=`` headers in CSRF
  origin check so browser POSTs behind the fleet reverse proxy are no
  longer rejected with 403 Forbidden.
