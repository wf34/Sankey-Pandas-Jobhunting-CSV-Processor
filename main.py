#!/usr/bin/env python3

from typing import Literal

import pandas as pd
import plotly.graph_objects as go
from tap import Tap


class Args(Tap):
    input_paths: list[str]  # CSV or TSV files with one row per application; rows of all files are combined
    format: Literal['png', 'svg'] = 'png'  # Image format of the rendered diagram
    output: str | None = None  # Path of the rendered image (default: sankey_diagram.<format>)

    def configure(self):
        self.add_argument('input_paths', nargs='+')


def load_applications(input_paths):
    """
    Load and combine application rows from CSV or TSV files.

    Expected columns: with_take_home_assignment (0/1), done_assignment (0/1),
    total_interviews (int, counts every call including the first HR call and the
    take-home review call) and outcome. Other columns, e.g. company_name and
    industry, are kept in the data but not plotted.

    Args:
        input_paths (list[str]): Paths to the input files; '.tsv' files are read as tab-separated.

    Returns:
        pd.DataFrame: One row per application.
    """
    frames = [pd.read_csv(path, sep='\t' if path.lower().endswith('.tsv') else ',')
              for path in input_paths]
    df = pd.concat(frames, ignore_index=True)

    df['with_take_home_assignment'] = df['with_take_home_assignment'].fillna(0).astype(int).astype(bool)
    df['done_assignment'] = df['done_assignment'].fillna(0).astype(int).astype(bool)
    df['total_interviews'] = df['total_interviews'].fillna(0).astype(int)
    # Outcomes are whatever the input contains, normalized to title case
    df['outcome'] = df['outcome'].str.strip().str.title().fillna('Pending')
    return df


def application_path(row):
    """
    List the stages one application went through, ending with its outcome.

    The first interview is the HR or hiring manager call. A take-home task comes after
    the other interviews, and when it was done its review call is the last interview.
    Placing the task at the same point for every application keeps the diagram acyclic.

    Args:
        row (pd.Series): A row from the DataFrame.

    Returns:
        list[str]: Stage names from 'Application' to the outcome.
    """
    total = row['total_interviews']
    if row['done_assignment'] and not row['with_take_home_assignment']:
        raise ValueError(f"{row.get('company_name')}: done_assignment without with_take_home_assignment")
    if row['done_assignment'] and total < 2:
        raise ValueError(f"{row.get('company_name')}: a done take-home needs the HR call and a review call, "
                         f"but total_interviews is {total}")

    # Interviews numbered 2 and on, excluding the review call
    numbered_interviews = total - 1 - int(row['done_assignment']) if total else 0
    path = ['Application']
    if total >= 1:
        path.append('HR or Hiring Manager Call')
    path += [f'Interview {number}' for number in range(2, 2 + numbered_interviews)]
    if row['with_take_home_assignment']:
        path.append('Take-Home Task')
    if row['done_assignment']:
        path.append('Take-Home Task Review Call')
    path.append(row['outcome'])
    return path


def create_sankey_df(df):
    """
    Create a DataFrame for Sankey diagram data.

    Args:
        df (pd.DataFrame): Application rows as returned by load_applications.

    Returns:
        pd.DataFrame: DataFrame containing sources, targets, and values for the Sankey diagram.
    """
    steps = [(source, target)
             for path in df.apply(application_path, axis=1)
             for source, target in zip(path, path[1:])]
    links = pd.DataFrame(steps, columns=['Source', 'Target'])
    return links.groupby(['Source', 'Target'], sort=False).size().reset_index(name='Value')


def generate_sankey_image(data, filename, format='svg'):
    """
    Generate and save a Sankey diagram as an image.

    Args:
        data (pd.DataFrame): DataFrame containing sources, targets, and values for the Sankey diagram.
        filename (str): Path of the image to write.
        format (str): The image format to save, 'svg' or 'png' (default is 'svg').

    Returns:
        None
    """
    # Prepare data
    # Keep first-seen order so node placement is the same on every run
    labels = list(dict.fromkeys(
        list(data['Source']) + list(data['Target'])))
    label_indices = {label: i for i, label in enumerate(labels)}

    # Create sources, targets, and values arrays for Plotly
    sources = data['Source'].map(label_indices).tolist()
    targets = data['Target'].map(label_indices).tolist()
    values = data['Value'].tolist()

    # Plotly only shows node totals on hover, so print them in the label as SankeyMATIC does.
    # A node's total is its larger side: inflow for outcomes, outflow for the first stage
    inflow = data.groupby('Target')['Value'].sum()
    outflow = data.groupby('Source')['Value'].sum()
    display_labels = [f"{label} {max(inflow.get(label, 0), outflow.get(label, 0))}"
                      for label in labels]

    # Generate a list of colors with 50% opacity
    color_palette = [
        'rgba(166,206,227,0.5)',  # Pale Blue
        'rgba(31,120,180,0.5)',   # Strong Blue
        'rgba(178,223,138,0.5)',  # Pale Green
        'rgba(51,160,44,0.5)',    # Strong Green
        'rgba(251,154,153,0.5)',  # Pale Red
        'rgba(227,26,28,0.5)',    # Strong Red
        'rgba(253,191,111,0.5)',  # Pale Orange
        'rgba(255,127,0,0.5)',    # Strong Orange
        'rgba(202,178,214,0.5)',  # Pale Purple
        'rgba(106,61,154,0.5)',   # Strong Purple
        'rgba(255,255,153,0.5)',  # Light Yellow
        'rgba(177,89,40,0.5)',    # Dark Brown
        'rgba(0,0,0,0.5)',        # Black
        'rgba(177,179,0,0.5)'     # Olive
    ]

    # Cycle through the color palette if there are more links than colors
    link_colors = [color_palette[i % len(color_palette)]
                   for i in range(len(sources))]

    # Create the Sankey diagram
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            # Nodes are thin black bars; the colored links carry the color
            thickness=2,
            color="black",
            line=dict(width=0),
            label=display_labels
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors  # Apply the generated colors with opacity
        ))])

    fig.update_layout(title_text="", font_size=10)
    # Save as an image in the chosen format
    # PNG is rendered at 4x so text and links stay sharp
    scale = 1 if format == 'svg' else 4
    fig.write_image(filename, format=format, width=1200, height=700, scale=scale)
    print(f"Sankey diagram saved as {filename}")


def main():
    args = Args().parse_args()
    output = args.output or f'sankey_diagram.{args.format}'
    df = load_applications(args.input_paths)
    df_sankey = create_sankey_df(df)
    generate_sankey_image(df_sankey, output, args.format)


if __name__ == "__main__":
    main()
