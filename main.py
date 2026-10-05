#!/usr/bin/env python3

import re
import xml.etree.ElementTree as ET
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
    path += [f'Interview #{number}' for number in range(2, 2 + numbered_interviews)]
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


WIDTH, HEIGHT = 1200, 700
MARGIN = dict(l=80, r=80, t=100, b=80)
SVG_NS = '{http://www.w3.org/2000/svg}'


LINK_OPACITY = 0.5

# Fixed 'r,g,b' colors of the flows into final outcomes, keyed by the title-cased outcome
OUTCOME_COLORS = {
    'Offer': '51,160,44',      # Green
    'Rejected': '227,26,28',   # Red
    'Ghosted': '128,128,128',  # Gray
    'Stopped': '255,170,0',    # Amber
}

# Colors for every other destination; none of them is close to an outcome color
STAGE_PALETTE = [
    '31,120,180',   # Blue
    '106,61,154',   # Purple
    '0,150,136',    # Teal
    '197,27,138',   # Magenta
    '177,89,40',    # Brown
    '23,190,207',   # Cyan
    '63,81,181',    # Indigo
    '247,129,191',  # Pink
    '166,206,227',  # Pale Blue
    '202,178,214',  # Pale Purple
    '128,203,196',  # Pale Teal
    '177,179,0',    # Olive
]


def assign_target_colors(targets):
    """
    Pick one color per destination node, shared by all links flowing into it.

    Outcomes listed in OUTCOME_COLORS get their fixed color. The other destinations take
    palette colors in order, so the colors are the same on every run.

    Args:
        targets (list[str]): Unique destination node names.

    Returns:
        dict[str, str]: Node name to an 'r,g,b' color.
    """
    colors = {}
    palette_position = 0
    for target in targets:
        if target in OUTCOME_COLORS:
            colors[target] = OUTCOME_COLORS[target]
        else:
            # Cycle through the palette if there are more destinations than colors
            colors[target] = STAGE_PALETTE[palette_position % len(STAGE_PALETTE)]
            palette_position += 1
    return colors


def measure_nodes(fig):
    """
    Find where Plotly places each node by rendering the figure to SVG.

    Args:
        fig (go.Figure): Sankey figure whose node labels are the node indices.

    Returns:
        dict[int, tuple[float, float, float, float]]: Node index to (x, y, width, height)
        in pixels, relative to the top-left corner of the plot area.
    """
    svg = ET.fromstring(fig.to_image(format='svg', width=WIDTH, height=HEIGHT))
    boxes = {}
    for group in svg.iter(f'{SVG_NS}g'):
        if group.get('class') != 'sankey-node':
            continue
        x, y = map(float, re.match(r'translate\(([-\d.]+),([-\d.]+)\)', group.get('transform')).groups())
        rect = group.find(f'{SVG_NS}rect')
        index = int(''.join(group.find(f'{SVG_NS}text').itertext()))
        boxes[index] = (x, y, float(rect.get('width')), float(rect.get('height')))
    return boxes


def label_annotations(boxes, labels):
    """
    Build node labels as annotations on a half-transparent white background.

    Labels sit right of their node, except in the last column where they sit left of it.

    Args:
        boxes (dict): Node boxes as returned by measure_nodes.
        labels (list[str]): Label text per node index.

    Returns:
        list[dict]: Plotly layout annotations.
    """
    plot_width = WIDTH - MARGIN['l'] - MARGIN['r']
    plot_height = HEIGHT - MARGIN['t'] - MARGIN['b']
    last_column_x = max(x for x, _, _, _ in boxes.values())
    gap = 4
    annotations = []
    for index, (x, y, width, height) in boxes.items():
        in_last_column = x == last_column_x
        annotations.append(dict(
            text=labels[index],
            xref='paper', yref='paper',
            x=(x - gap if in_last_column else x + width + gap) / plot_width,
            y=1 - (y + height / 2) / plot_height,
            xanchor='right' if in_last_column else 'left',
            yanchor='middle',
            align='left',
            showarrow=False,
            bgcolor='rgba(255,255,255,0.5)',
            borderpad=3,
        ))
    return annotations


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
    display_labels = [f"{label}<br>total: {max(inflow.get(label, 0), outflow.get(label, 0))}"
                      for label in labels]

    target_colors = assign_target_colors(list(dict.fromkeys(data['Target'])))

    # Links are colored by their destination, so everything arriving at a node reads as one flow
    link_colors = [f'rgba({target_colors[target]},{LINK_OPACITY})'
                   for target in data['Target']]

    # Create the Sankey diagram
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            # Nodes are thin black bars; the colored links carry the color
            thickness=2,
            color="black",
            line=dict(width=0),
            # Placeholder labels identify nodes in the measuring render below
            label=[str(i) for i in range(len(labels))]
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors  # Apply the generated colors with opacity
        ))])

    fig.update_layout(title_text="", font_size=10, margin=MARGIN)

    # Plotly's own node labels can't have a background, so draw them as annotations instead
    boxes = measure_nodes(fig)
    fig.update_traces(node_label=[''] * len(labels))
    fig.update_layout(annotations=label_annotations(boxes, display_labels))

    # Save as an image in the chosen format
    # PNG is rendered at 4x so text and links stay sharp
    scale = 1 if format == 'svg' else 4
    fig.write_image(filename, format=format, width=WIDTH, height=HEIGHT, scale=scale)
    print(f"Sankey diagram saved as {filename}")


def main():
    args = Args().parse_args()
    output = args.output or f'sankey_diagram.{args.format}'
    df = load_applications(args.input_paths)
    df_sankey = create_sankey_df(df)
    generate_sankey_image(df_sankey, output, args.format)


if __name__ == "__main__":
    main()
