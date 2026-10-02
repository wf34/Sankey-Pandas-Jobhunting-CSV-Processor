#!/usr/bin/env python3
"""
Interactively convert a job-search sheet into the TSV format main.py renders.

Each process is a row whose first column is the company and second column is a
free-text status. Rows below it with an empty first column belong to the same
company (other positions there); the company's status may sit on one of them.
For each company the script shows what happened and asks for the details,
appending every answer to <input>.reformat.tsv right away.
"""

import csv
import readline  # noqa: F401 -- gives input() line editing and history
import sys
from pathlib import Path

from tap import Tap

OUTPUT_COLUMNS = ['company_name', 'industry', 'had_recommender', 'with_take_home_assignment',
                  'done_assignment', 'total_interviews', 'outcome']
KNOWN_OUTCOMES = ['ghosted', 'rejected', 'stopped', 'offer', 'pending']
# Statuses meaning nothing was sent to the company
NOT_APPLIED = {'-'}


class Args(Tap):
    input_path: str  # Sheet export in TSV: company in column 1, what happened in column 2

    def configure(self):
        self.add_argument('input_path')


def read_companies(input_path):
    """
    Group sheet rows into one entry per company.

    Args:
        input_path (str): Path to the TSV sheet.

    Returns:
        list[dict]: Entries with 'line', 'company' and 'statuses' (non-empty column 2 values).
    """
    with open(input_path, newline='', encoding='utf-8') as file:
        rows = [[cell.strip() for cell in row] + ['', ''] for row in csv.reader(file, delimiter='\t')]

    companies = []
    for line, row in enumerate(rows, start=1):
        company, status = row[0], row[1]
        if line == 1 and status.lower() == 'status':
            continue
        if company or not companies or (status and companies[-1]['statuses']):
            # A status under a company that already has one is a separate, unnamed process
            companies.append({'line': line, 'company': company, 'statuses': []})
        if status:
            companies[-1]['statuses'].append(status)
    return [entry for entry in companies if entry['company'] or entry['statuses']]


def ask(prompt, parse, default=None, choices=None):
    """
    Ask until the answer parses.

    Args:
        prompt (str): Question shown to the user.
        parse (callable): Turns the answer into a value or raises ValueError with the reason.
        default (str | None): Answer used when the user just presses Enter.
        choices (list[str] | None): Numbered options listed under the question.

    Returns:
        The parsed answer.
    """
    suffix = f' [{default}]' if default is not None else ''
    if choices is not None:
        print(f'{prompt}{suffix}')
        if choices:
            print('  ' + '  '.join(f'{number}) {choice}' for number, choice in enumerate(choices, start=1)))
    while True:
        answer = input('  > ' if choices is not None else f'{prompt}{suffix}: ').strip()
        if not answer and default is not None:
            answer = default
        try:
            return parse(answer)
        except ValueError as error:
            print(f'  {error}')


def parse_yes_no(answer):
    if answer.lower() in ('y', 'yes'):
        return 1
    if answer.lower() in ('n', 'no'):
        return 0
    raise ValueError('answer y or n')


def parse_required(answer):
    if not answer:
        raise ValueError('an answer is needed')
    return answer


def parse_count(minimum):
    def parse(answer):
        if not answer.isdigit() or int(answer) < minimum:
            raise ValueError(f'expected a whole number, at least {minimum}')
        return int(answer)
    return parse


def parse_choice(choices, allow_skip=False):
    """Accept a listed number or any new text; '-' means skip when allowed."""
    def parse(answer):
        if allow_skip and answer == '-':
            return None
        if answer.isdigit():
            if not 1 <= int(answer) <= len(choices):
                raise ValueError(f'no choice number {answer}')
            return choices[int(answer) - 1]
        if not answer:
            raise ValueError('expected a number from the list or a new value')
        if answer not in choices:
            choices.append(answer)
        return answer
    return parse


def guess_outcome(statuses):
    """Pick the outcome keyword mentioned last in the status text, if any."""
    text = ' / '.join(statuses).lower()
    keywords = {'offer': 'offer', 'reject': 'rejected', 'ghost': 'ghosted',
                'stop': 'stopped', 'pending': 'pending'}
    found = [(text.rfind(keyword), outcome) for keyword, outcome in keywords.items() if keyword in text]
    return max(found)[1] if found else None


def ask_process(entry, industries, outcomes):
    """
    Ask about one company's process.

    Returns:
        list | None: Output row in OUTPUT_COLUMNS order, or None when skipped.
    """
    company = entry['company'] or ask('company name', parse_required)
    industry = ask('industry (- to skip this company)', parse_choice(industries, allow_skip=True),
                   choices=industries)
    if industry is None:
        return None
    had_recommender = ask('had a recommender? (y/n)', parse_yes_no, default='n')
    with_take_home = ask('was there a take-home assignment? (y/n)', parse_yes_no, default='n')
    done_assignment = ask('did you do it? (y/n)', parse_yes_no) if with_take_home else 0
    # main.py counts the HR call and the take-home review call among the interviews
    total_interviews = ask('how many interviews total, HR call included'
                           + (' and the take-home review call' if done_assignment else ''),
                           parse_count(2 if done_assignment else 0))
    outcome = ask('outcome', parse_choice(outcomes), default=guess_outcome(entry['statuses']),
                  choices=outcomes)
    return [company, industry, had_recommender, with_take_home, done_assignment, total_interviews, outcome]


def main():
    args = Args().parse_args()
    input_path = Path(args.input_path)
    output_path = input_path.with_name(input_path.name.removesuffix('.tsv') + '.reformat.tsv')
    companies = read_companies(input_path)

    done_companies = set()
    industries = []
    outcomes = list(KNOWN_OUTCOMES)
    if output_path.exists():
        print(f'{output_path} already exists and is never overwritten.')
        if ask('append to it, skipping the companies already there? (y/n)', parse_yes_no, default='n') == 0:
            sys.exit(1)
        with open(output_path, newline='', encoding='utf-8') as file:
            for row in csv.DictReader(file, delimiter='\t'):
                done_companies.add(row['company_name'])
                for value, choices in ((row['industry'], industries), (row['outcome'], outcomes)):
                    if value not in choices:
                        choices.append(value)

    is_new = not output_path.exists()
    with open(output_path, 'a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file, delimiter='\t', lineterminator='\n')
        if is_new:
            writer.writerow(OUTPUT_COLUMNS)
            file.flush()
        for number, entry in enumerate(companies, start=1):
            if entry['company'] and entry['company'] in done_companies:
                continue
            print(f"\n[{number}/{len(companies)}] line {entry['line']}: {entry['company'] or '(no company name)'}")
            for status in entry['statuses'] or ['(no status)']:
                print(f'    {status}')
            if set(entry['statuses']) <= NOT_APPLIED and entry['statuses']:
                print('  not applied, skipped')
                continue
            row = ask_process(entry, industries, outcomes)
            if row is not None:
                writer.writerow(row)
                file.flush()
    print(f'\nWritten to {output_path}')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nStopped; answers given so far are saved.')
        sys.exit(130)
