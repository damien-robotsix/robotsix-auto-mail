Extracted shared OAuth2 setup logic from ``ImapClient`` and ``SmtpClient``
constructors into the ``_ProtocolClient`` base class, removing 12 lines of
duplicate boilerplate.
