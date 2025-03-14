import pandas as pd

from .district_heating import predict_district_heating_emissions
from .electricity import predict_electricity_consumption_emissions
from . import calcfunc


SECTORS = {
    'BuildingHeating': dict(name='Rakennusten lämmitys'),
    'Transportation': dict(name='Liikenne'),
    'ElectricityConsumption': dict(name='Kulutussähkö'),
    'Waste': dict(name='Jätteiden käsittely'),
    'Industry': dict(name='Teollisuus ja työkoneet'),
    'Agriculture': dict(name='Maatalous'),
}
for key, val in SECTORS.items():
    val['subsectors'] = {}

HEATING_SUBSECTORS = {
    'DistrictHeat': 'Kaukolämpö',
    'OilHeating': 'Öljylämmitys',
    'ElectricityHeating': 'Sähkölämmitys',
    'GeothermalHeating': 'Maalämpö',
}
for key, val in HEATING_SUBSECTORS.items():
    SECTORS['BuildingHeating']['subsectors'][key] = dict(name=val)

TRANSPORTATION_SUBSECTORS = {
    'Cars': 'Henkilöautot',
    'Trucks': 'Kuorma-autot',
    'OtherTransportation': 'Muu liikenne',
}
for key, val in TRANSPORTATION_SUBSECTORS.items():
    SECTORS['Transportation']['subsectors'][key] = dict(name=val)


TARGETS = {
    'DistrictHeat': (754.589056908339, 250.733198734865),
    'OilHeating': (16.1569293157852, 0.0),
    'ElectricityHeating': (51.0638673954148, 29.7160855925585),
    'GeothermalHeating': (0, 0),
    'ElectricityConsumption': (242.663770299608, 150.979312657901),
    # ('Liikenne', 262.55592574098, 229.655246625791),
    'Cars': (128, 118.98),
    'Trucks': (60, 49.47),
    'OtherTransportation': (74.55, 61.2),
    'Industry': (3.23358058861613, 2.62034901128448),
    'Waste': (60.5886441012345, 50.6492489473935),
    'Agriculture': (0.637983301315191, 0.55519935555745),
}


@calcfunc(
    datasets=dict(
        ghg_emissions='jyrjola/hsy/pks_khk_paastot',
    ),
)
def prepare_emissions_dataset(datasets) -> pd.DataFrame:
    df = datasets['ghg_emissions']
    df = df[df.Kaupunki == 'Helsinki'].drop(columns='Kaupunki')
    df = df.set_index('Vuosi').copy()
    df = df.reset_index().groupby(['Vuosi', 'Sektori1', 'Sektori2', 'Sektori3'])['Päästöt'].sum().reset_index()

    df = df.rename(columns=dict(
        Sektori1='Sector1', Sektori2='Sector2', Sektori3='Sector3', Päästöt='Emissions', Vuosi='Year'
    ))

    sec1_renames = {val['name']: key for key, val in SECTORS.items()}
    sec1_renames['Lämmitys'] = 'BuildingHeating'
    sec1_renames['Sähkö'] = 'ElectricityConsumption'
    df['Sector1'] = df['Sector1'].map(lambda x: sec1_renames[x])

    sec2_renames = {}
    for sector in SECTORS.values():
        sec2_renames.update({val['name']: key for key, val in sector['subsectors'].items()})
    df['Sector2'] = df['Sector2'].map(lambda x: sec2_renames.get(x, ''))
    df['Sector3'] = df['Sector3'].map(lambda x: sec2_renames.get(x, ''))

    # Move transportation sectors one hierarchy level up
    df.loc[df.Sector1 == 'Transportation', 'Sector2'] = df['Sector3']

    df = df.groupby(['Year', 'Sector1', 'Sector2']).sum().reset_index()
    df.loc[(df.Sector1 == 'Transportation') & (df.Sector2 == ''), 'Sector2'] = 'OtherTransportation'
    df['Sector'] = list(zip(df.Sector1, df.Sector2))
    df = df.drop(columns=['Sector1', 'Sector2'])

    df = df.pivot(index='Year', columns='Sector', values='Emissions')
    return df


@calcfunc(
    variables=['target_year'],
    datasets=dict(),
    funcs=[
        prepare_emissions_dataset, predict_district_heating_emissions,
        predict_electricity_consumption_emissions
    ],
)
def generate_emissions_forecast(variables, datasets):
    df = prepare_emissions_dataset()

    last_historical_year = df.index.max()

    for year in range(df.index.max() + 1, variables['target_year'] + 1):
        df.loc[year] = None

    subsector_map = {}
    for sec_name, sector in SECTORS.items():
        for subsector_name, subsector in sector['subsectors'].items():
            subsector_map[subsector_name] = dict(supersector=sec_name)

    target_map = {}
    for key, val in TARGETS.items():
        if key in SECTORS:
            key = (key, '')
        else:
            key = (subsector_map[key]['supersector'], key)
        target_map[key] = val

    df.loc[2030] = [target_map[key][0] for key in df.columns]
    df.loc[2035] = [target_map[key][1] for key in df.columns]
    df = df.interpolate()

    pdf = predict_district_heating_emissions()
    df.loc[df.index > last_historical_year, ('BuildingHeating', 'DistrictHeat')] = \
        pdf.loc[pdf.index > last_historical_year, 'District heat consumption emissions']

    pdf = predict_electricity_consumption_emissions()
    df.loc[df.index > last_historical_year, ('ElectricityConsumption', '')] = \
        pdf.loc[pdf.index > last_historical_year, 'Emissions']

    # FIXME: Plug other emission prediction models

    # df['Total'] = df.sum(axis=1)
    df = df.reset_index().melt(id_vars=['Year'], value_name='Emissions', var_name='Sector')
    df.loc[df.Year <= last_historical_year, 'Forecast'] = False
    df.loc[df.Year > last_historical_year, 'Forecast'] = True
    df['Sector1'] = df.Sector.map(lambda x: x[0])
    df['Sector2'] = df.Sector.map(lambda x: x[1])
    df = df.drop(columns='Sector')

    return df


if __name__ == '__main__':
    pd.set_option('display.max_rows', None)
    df = generate_emissions_forecast()
    print(df)
    #df = df.set_index(['Sector1', 'Sector2'])
    #print(df)
