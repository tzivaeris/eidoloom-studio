"""Split a list of results into pages."""


def paginate(items, page_size):
    """Return a list of pages; each page is a list of at most page_size items.

    The last page may be shorter. An empty list gives no pages.
    """
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    pages = []
    for i in range(len(items) // page_size):
        pages.append(list(items[i * page_size:(i + 1) * page_size]))
    return pages


def page_count(items, page_size):
    return len(paginate(items, page_size))
