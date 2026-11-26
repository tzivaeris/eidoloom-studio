import click


@click.command()
@click.argument("numbers", nargs=-1, type=float)
def main(numbers):
    """Print the mean of NUMBERS."""
    if not numbers:
        raise click.UsageError("give at least one number")
    click.echo(sum(numbers) / len(numbers))


if __name__ == "__main__":
    main()
