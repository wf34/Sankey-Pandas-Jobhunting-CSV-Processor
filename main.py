#!/usr/bin/env python3

import math
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
    scale: float = 2  # PNG pixels per diagram unit; a PNG shown much smaller than its pixel size looks jagged

    def configure(self):
        self.add_argument('input_paths', nargs='+')


# Outcomes shown under a different name than the title-cased one from the input
OUTCOME_NAMES = {
    'Stopped': 'Withdrew',
}


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
    df['outcome'] = df['outcome'].str.strip().str.title().fillna('Pending').replace(OUTCOME_NAMES)
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


# Size of the area the nodes and links are laid out in; the image adds only the margins
PLOT_WIDTH, PLOT_HEIGHT = 1040, 520
# Blank space kept around the diagram
PADDING = 2
SVG_NS = '{http://www.w3.org/2000/svg}'


LINK_OPACITY = 0.5

# Fixed 'r,g,b' colors of the flows into final outcomes, keyed by the outcome as displayed
OUTCOME_COLORS = {
    'Offer': '51,160,44',      # Green
    'Rejected': '227,26,28',   # Red
    'Ghosted': '128,128,128',  # Gray
    'Withdrew': '255,170,0',   # Amber
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


def image_size(margin):
    """
    Size of the image that leaves exactly the plot area inside the given margins.

    Args:
        margin: Plotly margin with l, r, t and b in pixels.

    Returns:
        dict[str, int]: Image 'width' and 'height' in pixels.
    """
    return dict(width=PLOT_WIDTH + margin['l'] + margin['r'],
                height=PLOT_HEIGHT + margin['t'] + margin['b'])


def label_rectangle(annotation, size):
    """
    Locate a label in the plot area.

    Args:
        annotation (dict): A label as returned by label_annotations.
        size (tuple[float, float]): Its size as returned by measure_labels.

    Returns:
        tuple[float, float, float, float]: (left, top, width, height) in pixels, relative
        to the top-left corner of the plot area.
    """
    width, height = size
    anchor_x = annotation['x'] * PLOT_WIDTH
    left = anchor_x - width if annotation['xanchor'] == 'right' else anchor_x
    top = (1 - annotation['y']) * PLOT_HEIGHT - height / 2
    return left, top, width, height


def separate_labels(annotations, sizes):
    """
    Move overlapping labels apart vertically, each by half of the overlap.

    Args:
        annotations (list[dict]): Labels as returned by label_annotations; changed in place.
        sizes (list[tuple[float, float]]): Label sizes as returned by measure_labels.

    Returns:
        None
    """
    # Moving one pair apart can push a label into a third one, so repeat a few times
    for _ in range(10):
        moved = False
        for first in range(len(annotations)):
            for second in range(first + 1, len(annotations)):
                left_1, top_1, width_1, height_1 = label_rectangle(annotations[first], sizes[first])
                left_2, top_2, width_2, height_2 = label_rectangle(annotations[second], sizes[second])
                if left_1 >= left_2 + width_2 or left_2 >= left_1 + width_1:
                    continue
                upper, lower = (first, second) if top_1 <= top_2 else (second, first)
                overlap = min(top_1 + height_1, top_2 + height_2) - max(top_1, top_2)
                if overlap <= 0:
                    continue
                annotations[upper]['y'] += overlap / 2 / PLOT_HEIGHT
                annotations[lower]['y'] -= overlap / 2 / PLOT_HEIGHT
                moved = True
        if not moved:
            break


def fit_margin(annotations, sizes):
    """
    Find the smallest margins that still fit the labels.

    The labels of the first and last column lie outside the plot area, and a label can
    be taller than its node, so labels stick out of the plot area on every side.

    Args:
        annotations (list[dict]): Labels as returned by label_annotations.
        sizes (list[tuple[float, float]]): Label sizes as returned by measure_labels.

    Returns:
        dict[str, int]: Plotly margin with l, r, t and b in pixels.
    """
    overflow = dict(l=0, r=0, t=0, b=0)
    for annotation, size in zip(annotations, sizes):
        left, top, width, height = label_rectangle(annotation, size)
        overflow['l'] = max(overflow['l'], -left)
        overflow['r'] = max(overflow['r'], left + width - PLOT_WIDTH)
        overflow['t'] = max(overflow['t'], -top)
        overflow['b'] = max(overflow['b'], top + height - PLOT_HEIGHT)
    return {side: PADDING + math.ceil(extra) for side, extra in overflow.items()}


def measure_labels(fig):
    """
    Find the size Plotly gives each annotation by rendering the figure to SVG.

    Args:
        fig (go.Figure): Figure with the label annotations.

    Returns:
        list[tuple[float, float]]: (width, height) in pixels of each annotation's
        backdrop, in the order of the annotations.
    """
    svg = ET.fromstring(fig.to_image(format='svg', **image_size(fig.layout.margin)))
    sizes = {}
    for group in svg.iter(f'{SVG_NS}g'):
        if group.get('class') != 'annotation':
            continue
        rect = next(group.iter(f'{SVG_NS}rect'))
        # The backdrop has a 1px outline, drawn half inside the rectangle and half outside
        sizes[int(group.get('data-index'))] = (float(rect.get('width')) + 1, float(rect.get('height')) + 1)
    return [sizes[index] for index in sorted(sizes)]


def measure_nodes(fig):
    """
    Find where Plotly places each node by rendering the figure to SVG.

    Args:
        fig (go.Figure): Sankey figure whose node labels are the node indices.

    Returns:
        dict[int, tuple[float, float, float, float]]: Node index to (x, y, width, height)
        in pixels, relative to the top-left corner of the plot area.
    """
    svg = ET.fromstring(fig.to_image(format='svg', **image_size(fig.layout.margin)))
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

    Labels sit left of their node, where the flows arriving at it end, so a label can't be
    read as belonging to the next node. Only the outcomes in the last column have their
    label on the right, outside the diagram.

    Args:
        boxes (dict): Node boxes as returned by measure_nodes.
        labels (list[str]): Label text per node index.

    Returns:
        list[dict]: Plotly layout annotations.
    """
    last_column_x = max(x for x, _, _, _ in boxes.values())
    gap = 4
    annotations = []
    for index, (x, y, width, height) in boxes.items():
        in_last_column = x == last_column_x
        annotations.append(dict(
            text=labels[index],
            xref='paper', yref='paper',
            x=(x + width + gap if in_last_column else x - gap) / PLOT_WIDTH,
            y=1 - (y + height / 2) / PLOT_HEIGHT,
            xanchor='left' if in_last_column else 'right',
            yanchor='middle',
            # Text lines up along the side facing the node
            align='left' if in_last_column else 'right',
            showarrow=False,
            bgcolor='rgba(255,255,255,0.5)',
            borderpad=3,
        ))
    return annotations


def generate_sankey_image(data, filename, format='svg', scale=2):
    """
    Generate and save a Sankey diagram as an image.

    Args:
        data (pd.DataFrame): DataFrame containing sources, targets, and values for the Sankey diagram.
        filename (str): Path of the image to write.
        format (str): The image format to save, 'svg' or 'png' (default is 'svg').
        scale (float): PNG pixels per diagram unit; SVG is a vector image and ignores it.

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

    fig.update_layout(title_text="", font_size=10, margin=dict(l=PADDING, r=PADDING, t=PADDING, b=PADDING))

    # Plotly's own node labels can't have a background, so draw them as annotations instead
    boxes = measure_nodes(fig)
    fig.update_traces(node_label=[''] * len(labels))
    # The plot area keeps its size when the margins change, so the measured boxes stay valid
    annotations = label_annotations(boxes, display_labels)
    fig.update_layout(annotations=annotations)
    sizes = measure_labels(fig)
    separate_labels(annotations, sizes)
    margin = fit_margin(annotations, sizes)
    fig.update_layout(annotations=annotations, margin=margin)

    # Save as an image in the chosen format
    fig.write_image(filename, format=format, scale=1 if format == 'svg' else scale, **image_size(margin))
    print(f"Sankey diagram saved as {filename}")


def main():
    args = Args().parse_args()
    output = args.output or f'sankey_diagram.{args.format}'
    df = load_applications(args.input_paths)
    df_sankey = create_sankey_df(df)
    generate_sankey_image(df_sankey, output, args.format, args.scale)


if __name__ == "__main__":
    main()
