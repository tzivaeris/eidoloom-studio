# Contributing to Eidoloom Studio

Thank you for helping. Eidoloom Studio is licensed under the GNU AGPL 3.0 (see `LICENSE` and `NOTICE`).
Its author also offers it under a commercial licence, so every contribution needs the agreement below.

## Contributor licence agreement

By sending a contribution (a pull request, patch, or any code, text or file you submit for inclusion),
you agree that:

1. **It is yours to give.** You wrote it, or you have the right to submit it under these terms. If your
   employer has rights to your work, they have allowed you to contribute it.
2. **You keep your copyright.** You give John Chatzopoulos, the maintainer, and anyone who receives
   Eidoloom Studio from him, a permanent, worldwide, free, non-exclusive, irrevocable licence to use,
   copy, change, publish and distribute your contribution, and to license it to others under the AGPL
   3.0 **and under other licences, including commercial ones**.
3. **Patents.** If your contribution is covered by a patent you can license, you give the same people a
   permanent, worldwide, free licence under that patent for the contribution as part of Eidoloom Studio.
4. **No warranty.** You give it as it is, without warranty, and you are not obliged to support it.
5. **It stays open.** Every version of Eidoloom Studio that includes your contribution will also stay
   available under the AGPL 3.0 or a later version of it.

Say so in your first pull request, with this line in its description:

> I have read CONTRIBUTING.md and agree to its contributor licence agreement.

## How to work

- The server is `studio.py`, standard library only. Do not add pip packages to it.
- Run the tests before sending: `python3 tests/test_backend.py`, and the browser tests in
  `tests/browser/` if you changed `web/` (see `README.md`).
- Keep changes focused, and describe what you changed and why.
