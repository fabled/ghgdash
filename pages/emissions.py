import pandas as pd
import scipy.stats
import dash_table
import dash_core_components as dcc
import dash_html_components as html
import dash_bootstrap_components as dbc
from dash_table.Format import Format, Scheme
from dash.dependencies import Input, Output

from variables import get_variable, set_variable
from utils.colors import GHG_MAIN_SECTOR_COLORS_FI
from components.graphs import make_layout
from calc import calcfunc
from .base import Page


def find_consecutive_start(values):
    last_val = start_val = values[0]
    for val in values[1:]:
        if val - last_val != 1:
            start_val = val
        last_val = val
    return start_val


def interpolate_series(historical_series):
    s = historical_series

    res = scipy.stats.linregress(s.index, s)
    interpolated_series = pd.Series([res.intercept + res.slope * year for year in s.index], index=s.index)

    return interpolated_series


def generate_ghg_emission_graph(df):
    COLORS = {
        'Lämmitys': '#3E9FA8',
        'Sähkö': '#9FD9DA',
        'Liikenne': '#E9A5CA',
        'Teollisuus ja työkoneet': '#E281B6',
        'Jätteiden käsittely': '#9E266D',
        'Maatalous': '#680D48',
    }

    start_year = find_consecutive_start(df.index.unique())

    hist_df = df[~df.Forecast]
    hist_df = hist_df.loc[hist_df.index >= start_year]
    hist_df = hist_df.copy()

    latest_year = hist_df.loc[hist_df.index.max()]
    data_columns = list(latest_year.sort_values(ascending=False).index)
    data_columns.remove('Forecast')

    interpolated_traces = []
    for sector in data_columns:
        s = interpolate_series(hist_df[sector])
        interpolated_traces.append(dict(
            type='scatter',
            x=s.index,
            y=s,
            mode='lines',
            name=sector,
            line=dict(
                color=COLORS[sector]
            ),
            hoverinfo='none',
        ))

    hist_traces = [dict(
        type='scatter',
        x=hist_df.index,
        y=hist_df[sector],
        mode='markers',
        name=sector,
        line=dict(
            color=COLORS[sector]
        ),
        showlegend=False,
        hoverinfo='y+name',
        hovertemplate='%{x}: %{y:.0f} kt',
    ) for sector in data_columns]

    forecast_df = df.loc[df.Forecast | (df.index == hist_df.index.max())].copy()
    forecast_traces = [dict(
        type='scatter',
        x=forecast_df.index,
        y=forecast_df[sector],
        mode='lines',
        name=sector + ' (tavoite)',
        line=dict(
            color=COLORS[sector],
            dash='dash',
        ),
        hovertemplate='%{x}: %{y:.0f} kt',
        showlegend=False,
    ) for sector in data_columns]

    layout = make_layout(
        yaxis=dict(
            title='KHK-päästöt (kt CO₂-ekv.)'
        ),
        legend=dict(
            x=0.7,
            y=1,
            traceorder='normal',
            bgcolor='#fff',
        ),
    )

    fig = dict(data=hist_traces + interpolated_traces + forecast_traces, layout=layout)
    return fig


GHG_SECTOR_MAP = {
    'heating': 'Lämmitys',
    'electricity': 'Sähkö',
    'transport': 'Liikenne',
    'waste_management': 'Jätteiden käsittely',
    'industry': 'Teollisuus ja työkoneet',
}

ghg_sliders = []


def generate_ghg_sliders():
    weights = get_variable('ghg_reductions_weights')
    out = []
    for key, val in GHG_SECTOR_MAP.items():
        slider = dcc.Slider(
            id='ghg-%s-slider' % key,
            min=5,
            max=50,
            step=1,
            value=weights[key],
        )
        out.append(dbc.Col([
            html.Strong('%s' % val),
            slider
        ], md=12, className='mb-4'))
        ghg_sliders.append(slider)

    return dbc.Row(out)


@calcfunc(
    variables=[
        'target_year', 'ghg_reductions_reference_year', 'ghg_reductions_percentage_in_target_year',
        'ghg_reductions_weights',
    ],
    datasets=dict(
        ghg_emissions='jyrjola/hsy/pks_khk_paastot',
    )
)
def get_ghg_emissions_forecast(variables, datasets):
    target_year = variables['target_year']
    reference_year = variables['ghg_reductions_reference_year']
    reduction_percentage = variables['ghg_reductions_percentage_in_target_year']
    sector_weights = variables['ghg_reductions_weights']

    df = datasets['ghg_emissions']
    df = df[df.Kaupunki == 'Helsinki'].drop(columns='Kaupunki')
    df = df.set_index('Vuosi').copy()
    df = df.reset_index().groupby(['Vuosi', 'Sektori1'])['Päästöt'].sum().reset_index().set_index('Vuosi')

    ref_emissions = df[df.index == reference_year]['Päästöt'].sum()
    target_emissions = ref_emissions * (1 - (reduction_percentage / 100))
    last_emissions = dict(df.loc[[df.index.max()], ['Sektori1', 'Päästöt']].reset_index().set_index('Sektori1')['Päästöt'])

    other_sectors = [s for s in last_emissions.keys() if s not in GHG_SECTOR_MAP.values()]

    main_sector_emissions = sum([val for key, val in last_emissions.items() if key in GHG_SECTOR_MAP.values()])
    emission_shares = {sector_id: last_emissions[sector_name] / main_sector_emissions for sector_id, sector_name in GHG_SECTOR_MAP.items()}
    main_sector_target_emissions = target_emissions - sum([last_emissions[s] for s in other_sectors])

    target_year_emissions = {}

    weight_sum = sum(sector_weights.values())
    for sector_id, sector_name in GHG_SECTOR_MAP.items():
        weight = (sector_weights[sector_id] / weight_sum) * len(sector_weights)
        emission_shares[sector_id] /= weight

    sum_shares = sum(emission_shares.values())
    for key, val in emission_shares.items():
        emission_shares[key] = val / sum_shares

    for sector_id, sector_name in GHG_SECTOR_MAP.items():
        target = main_sector_target_emissions * emission_shares[sector_id]
        target_year_emissions[sector_name] = target

    for sector_name in other_sectors:
        target_year_emissions[sector_name] = last_emissions[sector_name]

    df = df.reset_index().set_index(['Vuosi', 'Sektori1']).unstack('Sektori1')
    df.columns = df.columns.get_level_values(1)
    last_historical_year = df.index.max()
    df.loc[target_year] = [target_year_emissions[x] for x in df.columns]
    df = df.reindex(range(df.index.min(), df.index.max() + 1))
    future = df.loc[df.index >= last_historical_year].interpolate()
    df.update(future)
    df.dropna(inplace=True)
    df.loc[df.index <= last_historical_year, 'Forecast'] = False
    df.loc[df.index > last_historical_year, 'Forecast'] = True
    return df.copy()


emissions_page = dbc.Row([
    dbc.Col([
        dbc.Row([
            dbc.Col([
                dbc.Card(className='mb-5', children=dbc.CardBody(
                    dcc.Graph(
                        id='ghg-emissions-graph',
                        config={
                            'displayModeBar': False,
                            'showLink': False,
                        }
                    ),
                ))
            ], md=8),
            dbc.Col([
                html.Div(generate_ghg_sliders(), id='ghg-sliders'),
            ], md=4, className='mt-4'),
        ]),
        dbc.Row([
            dbc.Col([
                html.Div(id='ghg-emissions-table-container'),
            ])
        ], className='mb-4'),
    ]),

    html.Div(id='emission-sectors-graphs')
])


def draw_emission_graph(df):
    start_year = find_consecutive_start(df.index.unique())

    hist_df = df.loc[df.Forecast & (df.index >= start_year)].copy()

    latest_hist_year = hist_df.loc[hist_df.index.max()]

    sector_name = df.name

    s = interpolate_series(hist_df.Emissions)
    regression_trace = dict(
        type='scatter',
        x=s.index,
        y=s,
        mode='lines',
        name=sector_name,
        line=dict(
            color=GHG_MAIN_SECTOR_COLORS_FI[sector_name]
        ),
        hoverinfo='none',
    )

    hist_trace = dict(
        type='scatter',
        x=hist_df.index,
        y=hist_df.Emissions,
        mode='markers',
        name=sector_name,
        line=dict(
            color=GHG_MAIN_SECTOR_COLORS_FI[sector_name]
        ),
        showlegend=False,
        hoverinfo='y+name',
    )

    forecast_df = df[df.Forecast].copy()
    forecast_trace = dict(
        type='scatter',
        x=forecast_df.index,
        y=forecast_df.Emissions,
        mode='lines',
        name=sector_name,
        line=dict(
            color='grey',
            dash='dash',
        ),
        showlegend=False,
    )

    layout = make_layout(
        yaxis=dict(
            title='kt (CO₂-ekv.)',
            rangemode='tozero',
        ),
        margin=dict(
            t=40,
            r=20,
        ),
        showlegend=False,
        title=sector_name
    )

    fig = dict(data=[regression_trace, hist_trace, forecast_trace], layout=layout)
    return fig


def render_page():
    df = get_ghg_emissions_forecast().copy()

    content = emissions_page

    sectors = list(df.columns)
    sectors.remove('Forecast')

    cols = []
    """
    for sector_name in sectors:
        sec_df = df[[sector_name, 'Forecast']]
        sec_df = sec_df.rename(columns={sector_name: 'Emissions'})
        sec_df.name = sector_name

        cols.append(dbc.Col([
            dbc.Card(dbc.CardBody(
                dcc.Graph(figure=draw_emission_graph(sec_df))
            ), className="mb-5")
        ], md=6))

    content['emission-sectors-graphs'].children = [
        dbc.Row(cols)
    ]
    """

    return content


page = Page(
    id='emissions',
    name='Kasvihuonekaasupäästöt',
    content=render_page,
    path='/',
)


@page.callback(
    outputs=[Output('ghg-emissions-graph', 'figure'), Output('ghg-emissions-table-container', 'children')],
    inputs=[Input(slider.id, 'value') for slider in ghg_sliders]
)
def ghg_slider_callback(*values):
    sectors = [x.id.split('-')[1] for x in ghg_sliders]
    new_values = {s: val for s, val in zip(sectors, values)}
    set_variable('ghg_reductions_weights', new_values)
    df = get_ghg_emissions_forecast().copy()
    fig = generate_ghg_emission_graph(df)

    df['Yhteensä'] = df.sum(axis=1)
    last_hist_year = df[~df.Forecast].index.max()
    data_columns = list(df.loc[df.index == last_hist_year].stack().sort_values(ascending=False).index.get_level_values(1))

    data_columns.remove('Forecast')
    data_columns.insert(0, 'Vuosi')
    data_columns.remove('Yhteensä')
    data_columns.append('Yhteensä')

    last_forecast_year = df[df.Forecast].index.max()
    table_df = df.loc[df.index.isin([last_hist_year, last_forecast_year - 5, last_forecast_year - 10, last_forecast_year])]
    table_data = table_df.reset_index().to_dict('rows')
    table_cols = []
    for col_name in data_columns:
        col = dict(id=col_name)
        if col_name == 'Vuosi':
            col['name'] = ['', 'Vuosi']
        else:
            col['type'] = 'numeric'
            col['format'] = Format(precision=0, group='', scheme=Scheme.fixed)
            col['name'] = ['Päästöt', col_name]
        table_cols.append(col)
    table = dash_table.DataTable(
        data=table_data,
        columns=table_cols,
        # style_as_list_view=True,
        style_cell={'padding': '5px'},
        style_header={
            'fontWeight': 'bold'
        },
        style_cell_conditional=[
            {
                'if': {'column_id': 'Vuosi'},
                'fontWeight': 'bold',
            }
        ],
        merge_duplicate_headers=True,
    )

    return [fig, table]
