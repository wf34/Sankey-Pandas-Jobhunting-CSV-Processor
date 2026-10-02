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

    Args:
        input_paths (list[str]): Paths to the input files; '.tsv' files are read as tab-separated.

    Returns:
        pd.DataFrame: One row per application, without 'Diary Update' rows.
    """
    frames = [pd.read_csv(path, sep='\t' if path.lower().endswith('.tsv') else ',')
              for path in input_paths]
    df = pd.concat(frames, ignore_index=True)

    # Stage columns are optional; a missing one means no application reached that stage
    for stage_column in ['Screening Date', 'First Interview', 'Second Interview', 'Third Interview']:
        if stage_column not in df.columns:
            df[stage_column] = pd.NA

    # Normalize column data to title case
    df['Outcome'] = df['Outcome'].str.title().fillna('Pending')

    # Filter out 'Diary Update' from 'Outcome'
    return df[df['Outcome'] != 'Diary Update'].copy()


def determine_transition(row):
    """
    Determine the transition based on the screening date.

    Args:
        row (pd.Series): A row from the DataFrame.

    Returns:
        str: The determined transition.
    """
    # Determine the transition based on screening date being non null value
    if pd.notna(row['Screening Date']):
        return 'Screening'
    return row['Outcome']


def summarize_stages(df):
    """
    Count where applications go after each stage.

    Args:
        df (pd.DataFrame): Application rows as returned by load_applications.

    Returns:
        tuple[pd.Series, ...]: Exit counts for application, screening and the first, second and third interviews.
    """
    # Apply transition determination across the DataFrame
    df['Application Exit'] = df.apply(determine_transition, axis=1)
    df['Screening Exit'] = df.apply(lambda row: 'First Interview' if pd.notna(
        row['First Interview']) else row['Outcome'], axis=1)
    df['First Interview Exit'] = df.apply(lambda row: 'Second Interview' if pd.notna(
        row['Second Interview']) else row['Outcome'], axis=1)
    df['Second Interview Exit'] = df.apply(lambda row: 'Third Interview' if pd.notna(
        row['Third Interview']) else row['Outcome'], axis=1)
    df['Third Interview Exit'] = df['Outcome']

    # Count transitions for various stages
    application_summary = df['Application Exit'].value_counts().sort_index()
    screening_summary = df.loc[pd.notna(
        df['Screening Date']), 'Screening Exit'].value_counts().sort_index()
    first_interview_summary = df.loc[pd.notna(
        df['First Interview']), 'First Interview Exit'].value_counts().sort_index()
    second_interview_summary = df.loc[pd.notna(
        df['Second Interview']), 'Second Interview Exit'].value_counts().sort_index()
    third_interview_summary = df.loc[pd.notna(
        df['Third Interview']), 'Third Interview Exit'].value_counts().sort_index()

    return (application_summary, screening_summary, first_interview_summary,
            second_interview_summary, third_interview_summary)


def create_sankey_df(application_summary, screening_summary, first_interview_summary, second_interview_summary, third_interview_summary):
    """
    Create a DataFrame for Sankey diagram data.

    Args:
        application_summary (pd.Series): Summary of application exits.
        screening_summary (pd.Series): Summary of screening exits.
        first_interview_summary (pd.Series): Summary of first interview exits.
        second_interview_summary (pd.Series): Summary of second interview exits.
        third_interview_summary (pd.Series): Summary of third interview exits.

    Returns:
        pd.DataFrame: DataFrame containing sources, targets, and values for the Sankey diagram.
    """
    # Initialize lists to build DataFrame
    sources = []
    targets = []
    values = []

    # Application to Screening/Outcome
    for key, value in application_summary.items():
        sources.append('Application')
        targets.append(key)
        values.append(value)

    # Screening to First Interview/Outcome
    for key, value in screening_summary.items():
        sources.append('Screening')
        targets.append(key)
        values.append(value)

    # First Interview to Second Interview/Outcome
    for key, value in first_interview_summary.items():
        sources.append('First Interview')
        targets.append(key)
        values.append(value)

    # Second Interview to Third Interview/Outcome
    for key, value in second_interview_summary.items():
        sources.append('Second Interview')
        targets.append(key)
        values.append(value)

    # Third Interview to Outcome
    for key, value in third_interview_summary.items():
        sources.append('Third Interview')
        targets.append(key)
        values.append(value)

    return pd.DataFrame({'Source': sources, 'Target': targets, 'Value': values})


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
            thickness=20,
            line=dict(color="black", width=0.5),
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
    df_sankey = create_sankey_df(*summarize_stages(df))
    generate_sankey_image(df_sankey, output, args.format)


if __name__ == "__main__":
    main()
