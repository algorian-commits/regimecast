"""Position Guardian (Phase 13C): watches users' open leveraged positions once a minute and warns them, in the
inbox and on Telegram, with a safer version of the position in the same message.

* ``crypto``       Fernet encryption of Binance keys with the server key ``GUARDIAN_SECRET``.
* ``hyperliquid``  positions by public wallet address (no key at all).
* ``binance``      positions by a read-only API key; keys that can trade or withdraw are refused.
* ``risk``         the read-out per position (24 h liquidation chance, distance, stress), the three alert rules
                   and the safer version (Phase 12's "fix my trade" applied to the position as it stands now).
* ``loop``         the once-a-minute loop: poll sources, check positions, write inbox rows, send Telegram.
"""
