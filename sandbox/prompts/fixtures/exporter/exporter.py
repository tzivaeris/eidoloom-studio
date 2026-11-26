def export_csv(rows, path):
    """Write a list of dicts to a CSV file."""
    if not rows:
        raise ValueError("nothing to export")
    header = list(rows[0].keys())
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(",".join(header) + "\n")
        for row in rows:
            fh.write(",".join(str(row.get(key, "")) for key in header) + "\n")
