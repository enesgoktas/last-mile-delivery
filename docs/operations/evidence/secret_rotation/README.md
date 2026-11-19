# Secret Rotation Evidence

Store secret-rotation execution evidence here.

Minimum evidence for a valid rotation:
- date/time UTC
- environment
- rotated secret or `kid`
- operator / reviewer
- deployment or workflow reference
- validation proof (auth refresh, tracking lookup, provider send test, or decrypt-read proof)
- retirement/revocation timestamp for the previous credential
