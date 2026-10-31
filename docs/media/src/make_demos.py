"""Build the throwaway git repos the README recordings run slop-gate against.

Every piece of slop here is deliberate. Hidden characters are written as
escapes so this script stays plain ASCII.

    python make_demos.py OUT_DIR
"""

import subprocess
import sys
from pathlib import Path

ZWSP = "​"  # zero-width space
RLO = "‮"  # right-to-left override (Trojan Source)
PDF = "‬"  # pop directional formatting
CYR_A = "а"  # Cyrillic small a, looks like Latin a


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "dev@example.com")
    git(path, "config", "user.name", "dev")
    return path


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


# --- hidden Unicode and residue: before and after ---------------------------

BILLING_BEFORE = f'''"""Invoice totals for the billing service."""


def total(items, discount=0):
    subtotal = sum(i.price * i.qty for i in items)
    rate = 0.08
    return round(subtotal * (1 - discount) * (1 + rate), 2)


def can_refund(user):
    return user.role == "admin{ZWSP}"  # {RLO}{PDF} owners only


def refund(invoice):
    # ... rest of the implementation unchanged
    p{CYR_A}id = invoice.paid
    return p{CYR_A}id


def notify(customer):
    # As an AI language model, I cannot send emails directly.
    try:
        customer.send("receipt")
    except Exception:
        pass
'''

BILLING_AFTER = '''"""Invoice totals for the billing service."""


def total(items, discount=0):
    subtotal = sum(i.price * i.qty for i in items)
    rate = 0.08
    return round(subtotal * (1 - discount) * (1 + rate), 2)


def can_refund(user):
    return user.role == "admin"


def refund(invoice):
    if not invoice.paid:
        raise ValueError(f"invoice {invoice.id} was never paid")
    return invoice.paid


def notify(customer):
    customer.send("receipt")
'''


def residue_repo(root: Path, fixed: bool) -> None:
    repo = new_repo(root / ("residue-after" if fixed else "residue-before"))
    (repo / "billing.py").write_text('"""Invoice totals for the billing service."""\n')
    commit(repo, "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "billing.py").write_text(BILLING_AFTER if fixed else BILLING_BEFORE)
    commit(repo, "Add totals, refunds and receipts")


# --- changed lines only ------------------------------------------------------

DOCS_OLD = """# Billing service

It's important to note that invoices are generated nightly.

In today's fast-paced digital landscape, billing plays a pivotal role.
"""

DOCS_NEW_LINE = """
## Refunds

This isn't just a refund endpoint, it's a game-changer for support.
"""


def scope_repo(root: Path) -> None:
    repo = new_repo(root / "scope")
    (repo / "README.md").write_text(DOCS_OLD)
    commit(repo, "Old docs, written before slop-gate")
    git(repo, "checkout", "-qb", "feature")
    with (repo / "README.md").open("a") as f:
        f.write(DOCS_NEW_LINE)
    commit(repo, "Document refunds")


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    residue_repo(out, fixed=False)
    residue_repo(out, fixed=True)
    scope_repo(out)
    print(out)


if __name__ == "__main__":
    main()
