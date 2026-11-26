"""Load the contacts file."""


def load_contacts(path):
    """Read a CSV file with a header row.

    Returns a list of dicts, one per record, keyed by the column names in the header.
    Every value is a string; an empty field is the empty string.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    header = lines[0].split(",")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split(",")
        rows.append(dict(zip(header, values)))
    return rows
