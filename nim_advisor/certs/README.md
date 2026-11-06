# Optional corporate CA certificates

If `docker compose build` fails with `CERTIFICATE_VERIFY_FAILED` (for example behind
Zscaler TLS inspection), export your company's root CA as a Base-64 PEM file and copy
it here with a `.crt` extension, e.g. `zscaler-root.crt`. The Dockerfile adds every
`*.crt` file in this folder to the container trust store, so TLS stays verified.

Export on Windows: `certmgr.msc` -> Trusted Root Certification Authorities ->
Certificates -> "Zscaler Root CA" -> All Tasks -> Export -> Base-64 encoded X.509 (.CER),
then rename the file to `zscaler-root.crt`.
