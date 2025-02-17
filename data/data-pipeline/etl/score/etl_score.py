import collections
import functools
import pandas as pd

from etl.base import ExtractTransformLoad
from utils import get_module_logger
from etl.sources.census.etl_utils import get_state_fips_codes

logger = get_module_logger(__name__)


class ScoreETL(ExtractTransformLoad):
    def __init__(self):
        # Define some global parameters
        self.BUCKET_SOCIOECONOMIC = "Socioeconomic Factors"
        self.BUCKET_SENSITIVE = "Sensitive populations"
        self.BUCKET_ENVIRONMENTAL = "Environmental effects"
        self.BUCKET_EXPOSURES = "Exposures"
        self.BUCKETS = [
            self.BUCKET_SOCIOECONOMIC,
            self.BUCKET_SENSITIVE,
            self.BUCKET_ENVIRONMENTAL,
            self.BUCKET_EXPOSURES,
        ]

        # A few specific field names
        # TODO: clean this up, I name some fields but not others.
        self.UNEMPLOYED_FIELD_NAME = "Unemployed civilians (percent)"
        self.LINGUISTIC_ISOLATION_FIELD_NAME = "Linguistic isolation (percent)"
        self.HOUSING_BURDEN_FIELD_NAME = "Housing burden (percent)"
        self.POVERTY_FIELD_NAME = (
            "Poverty (Less than 200% of federal poverty line)"
        )
        self.HIGH_SCHOOL_FIELD_NAME = "Percent individuals age 25 or over with less than high school degree"

        # There's another aggregation level (a second level of "buckets").
        self.AGGREGATION_POLLUTION = "Pollution Burden"
        self.AGGREGATION_POPULATION = "Population Characteristics"

        self.PERCENTILE_FIELD_SUFFIX = " (percentile)"
        self.MIN_MAX_FIELD_SUFFIX = " (min-max normalized)"

        self.SCORE_CSV_PATH = self.DATA_PATH / "score" / "csv" / "full"

        # dataframes
        self.df: pd.DataFrame
        self.ejscreen_df: pd.DataFrame
        self.census_df: pd.DataFrame
        self.housing_and_transportation_df: pd.DataFrame
        self.hud_housing_df: pd.DataFrame

    def extract(self) -> None:
        # EJSCreen csv Load
        ejscreen_csv = self.DATA_PATH / "dataset" / "ejscreen_2019" / "usa.csv"
        self.ejscreen_df = pd.read_csv(
            ejscreen_csv, dtype={"ID": "string"}, low_memory=False
        )
        self.ejscreen_df.rename(
            columns={"ID": self.GEOID_FIELD_NAME}, inplace=True
        )

        # Load census data
        census_csv = self.DATA_PATH / "dataset" / "census_acs_2019" / "usa.csv"
        self.census_df = pd.read_csv(
            census_csv,
            dtype={self.GEOID_FIELD_NAME: "string"},
            low_memory=False,
        )

        # Load housing and transportation data
        housing_and_transportation_index_csv = (
            self.DATA_PATH
            / "dataset"
            / "housing_and_transportation_index"
            / "usa.csv"
        )
        self.housing_and_transportation_df = pd.read_csv(
            housing_and_transportation_index_csv,
            dtype={self.GEOID_FIELD_NAME: "string"},
            low_memory=False,
        )

        # Load HUD housing data
        hud_housing_csv = self.DATA_PATH / "dataset" / "hud_housing" / "usa.csv"
        self.hud_housing_df = pd.read_csv(
            hud_housing_csv,
            dtype={self.GEOID_TRACT_FIELD_NAME: "string"},
            low_memory=False,
        )

    def transform(self) -> None:
        logger.info(f"Transforming Score Data")

        # Join all the data sources that use census block groups
        census_block_group_dfs = [
            self.ejscreen_df,
            self.census_df,
            self.housing_and_transportation_df,
        ]

        census_block_group_df = functools.reduce(
            lambda left, right: pd.merge(
                left=left, right=right, on=self.GEOID_FIELD_NAME, how="outer"
            ),
            census_block_group_dfs,
        )

        # Sanity check the join.
        if (
            len(census_block_group_df[self.GEOID_FIELD_NAME].str.len().unique())
            != 1
        ):
            raise ValueError(
                f"One of the input CSVs uses {self.GEOID_FIELD_NAME} with a different length."
            )

        # Join all the data sources that use census tracts
        # TODO: when there's more than one data source using census tract, reduce/merge them here.
        census_tract_df = self.hud_housing_df

        # Calculate the tract for the CBG data.
        census_block_group_df[
            self.GEOID_TRACT_FIELD_NAME
        ] = census_block_group_df[self.GEOID_FIELD_NAME].str[0:11]

        self.df = census_block_group_df.merge(
            census_tract_df, on=self.GEOID_TRACT_FIELD_NAME
        )

        if len(census_block_group_df) > 220333:
            raise ValueError("Too many rows in the join.")

        # Define a named tuple that will be used for each data set input.
        DataSet = collections.namedtuple(
            typename="DataSet",
            field_names=["input_field", "renamed_field", "bucket"],
        )

        data_sets = [
            # The following data sets have `bucket=None`, because it's not used in the bucket based score ("Score C").
            DataSet(
                input_field=self.GEOID_FIELD_NAME,
                # Use the name `GEOID10` to enable geoplatform.gov's workflow.
                renamed_field=self.GEOID_FIELD_NAME,
                bucket=None,
            ),
            DataSet(
                input_field=self.HOUSING_BURDEN_FIELD_NAME,
                renamed_field=self.HOUSING_BURDEN_FIELD_NAME,
                bucket=None,
            ),
            DataSet(
                input_field="ACSTOTPOP",
                renamed_field="Total population",
                bucket=None,
            ),
            # The following data sets have buckets, because they're used in the score
            DataSet(
                input_field="CANCER",
                renamed_field="Air toxics cancer risk",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="RESP",
                renamed_field="Respiratory hazard index",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="DSLPM",
                renamed_field="Diesel particulate matter",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="PM25",
                renamed_field="Particulate matter (PM2.5)",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="OZONE",
                renamed_field="Ozone",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="PTRAF",
                renamed_field="Traffic proximity and volume",
                bucket=self.BUCKET_EXPOSURES,
            ),
            DataSet(
                input_field="PRMP",
                renamed_field="Proximity to RMP sites",
                bucket=self.BUCKET_ENVIRONMENTAL,
            ),
            DataSet(
                input_field="PTSDF",
                renamed_field="Proximity to TSDF sites",
                bucket=self.BUCKET_ENVIRONMENTAL,
            ),
            DataSet(
                input_field="PNPL",
                renamed_field="Proximity to NPL sites",
                bucket=self.BUCKET_ENVIRONMENTAL,
            ),
            DataSet(
                input_field="PWDIS",
                renamed_field="Wastewater discharge",
                bucket=self.BUCKET_ENVIRONMENTAL,
            ),
            DataSet(
                input_field="PRE1960PCT",
                renamed_field="Percent pre-1960s housing (lead paint indicator)",
                bucket=self.BUCKET_ENVIRONMENTAL,
            ),
            DataSet(
                input_field="UNDER5PCT",
                renamed_field="Individuals under 5 years old",
                bucket=self.BUCKET_SENSITIVE,
            ),
            DataSet(
                input_field="OVER64PCT",
                renamed_field="Individuals over 64 years old",
                bucket=self.BUCKET_SENSITIVE,
            ),
            DataSet(
                input_field=self.LINGUISTIC_ISOLATION_FIELD_NAME,
                renamed_field=self.LINGUISTIC_ISOLATION_FIELD_NAME,
                bucket=self.BUCKET_SENSITIVE,
            ),
            DataSet(
                input_field="LINGISOPCT",
                renamed_field="Percent of households in linguistic isolation",
                bucket=self.BUCKET_SOCIOECONOMIC,
            ),
            DataSet(
                input_field="LOWINCPCT",
                renamed_field=self.POVERTY_FIELD_NAME,
                bucket=self.BUCKET_SOCIOECONOMIC,
            ),
            DataSet(
                input_field="LESSHSPCT",
                renamed_field=self.HIGH_SCHOOL_FIELD_NAME,
                bucket=self.BUCKET_SOCIOECONOMIC,
            ),
            DataSet(
                input_field=self.UNEMPLOYED_FIELD_NAME,
                renamed_field=self.UNEMPLOYED_FIELD_NAME,
                bucket=self.BUCKET_SOCIOECONOMIC,
            ),
            DataSet(
                input_field="ht_ami",
                renamed_field="Housing + Transportation Costs % Income for the Regional Typical Household",
                bucket=self.BUCKET_SOCIOECONOMIC,
            ),
        ]

        # Rename columns:
        renaming_dict = {
            data_set.input_field: data_set.renamed_field
            for data_set in data_sets
        }

        self.df.rename(
            columns=renaming_dict,
            inplace=True,
            errors="raise",
        )

        columns_to_keep = [data_set.renamed_field for data_set in data_sets]
        self.df = self.df[columns_to_keep]

        # Convert all columns to numeric.
        for data_set in data_sets:
            # Skip GEOID_FIELD_NAME, because it's a string.
            if data_set.renamed_field == self.GEOID_FIELD_NAME:
                continue
            self.df[f"{data_set.renamed_field}"] = pd.to_numeric(
                self.df[data_set.renamed_field]
            )

        # calculate percentiles
        for data_set in data_sets:
            self.df[
                f"{data_set.renamed_field}{self.PERCENTILE_FIELD_SUFFIX}"
            ] = self.df[data_set.renamed_field].rank(pct=True)

        # Math:
        # (
        #     Observed value
        #     - minimum of all values
        # )
        # divided by
        # (
        #    Maximum of all values
        #     - minimum of all values
        # )
        for data_set in data_sets:
            # Skip GEOID_FIELD_NAME, because it's a string.
            if data_set.renamed_field == self.GEOID_FIELD_NAME:
                continue

            min_value = self.df[data_set.renamed_field].min(skipna=True)

            max_value = self.df[data_set.renamed_field].max(skipna=True)

            logger.info(
                f"For data set {data_set.renamed_field}, the min value is {min_value} and the max value is {max_value}."
            )

            self.df[f"{data_set.renamed_field}{self.MIN_MAX_FIELD_SUFFIX}"] = (
                self.df[data_set.renamed_field] - min_value
            ) / (max_value - min_value)

            # Graph distributions and correlations.
            min_max_fields = [
                f"{data_set.renamed_field}{self.MIN_MAX_FIELD_SUFFIX}"
                for data_set in data_sets
                if data_set.renamed_field != self.GEOID_FIELD_NAME
            ]

        # Calculate score "A" and score "B"
        self.df["Score A"] = self.df[
            [
                "Poverty (Less than 200% of federal poverty line) (percentile)",
                "Percent individuals age 25 or over with less than high school degree (percentile)",
            ]
        ].mean(axis=1)
        self.df["Score B"] = (
            self.df[
                "Poverty (Less than 200% of federal poverty line) (percentile)"
            ]
            * self.df[
                "Percent individuals age 25 or over with less than high school degree (percentile)"
            ]
        )

        # Calculate "CalEnviroScreen for the US" score
        # Average all the percentile values in each bucket into a single score for each of the four buckets.
        for bucket in self.BUCKETS:
            fields_in_bucket = [
                f"{data_set.renamed_field}{self.PERCENTILE_FIELD_SUFFIX}"
                for data_set in data_sets
                if data_set.bucket == bucket
            ]
            self.df[f"{bucket}"] = self.df[fields_in_bucket].mean(axis=1)

        # Combine the score from the two Exposures and Environmental Effects buckets into a single score called "Pollution Burden". The math for this score is: (1.0 * Exposures Score + 0.5 * Environment Effects score) / 1.5.
        self.df[self.AGGREGATION_POLLUTION] = (
            1.0 * self.df[f"{self.BUCKET_EXPOSURES}"]
            + 0.5 * self.df[f"{self.BUCKET_ENVIRONMENTAL}"]
        ) / 1.5

        # Average the score from the two Sensitive populations and Socioeconomic factors buckets into a single score called "Population Characteristics".
        self.df[self.AGGREGATION_POPULATION] = self.df[
            [f"{self.BUCKET_SENSITIVE}", f"{self.BUCKET_SOCIOECONOMIC}"]
        ].mean(axis=1)

        # Multiply the "Pollution Burden" score and the "Population Characteristics" together to produce the cumulative impact score.
        self.df["Score C"] = (
            self.df[self.AGGREGATION_POLLUTION]
            * self.df[self.AGGREGATION_POPULATION]
        )

        if len(census_block_group_df) > 220333:
            raise ValueError("Too many rows in the join.")

        fields_to_use_in_score = [
            self.UNEMPLOYED_FIELD_NAME,
            self.LINGUISTIC_ISOLATION_FIELD_NAME,
            self.HOUSING_BURDEN_FIELD_NAME,
            self.POVERTY_FIELD_NAME,
            self.HIGH_SCHOOL_FIELD_NAME,
        ]

        fields_min_max = [
            f"{field}{self.MIN_MAX_FIELD_SUFFIX}"
            for field in fields_to_use_in_score
        ]
        fields_percentile = [
            f"{field}{self.PERCENTILE_FIELD_SUFFIX}"
            for field in fields_to_use_in_score
        ]

        # Calculate "Score D", which uses min-max normalization
        # and calculate "Score E", which uses percentile normalization for the same fields
        self.df["Score D"] = self.df[fields_min_max].mean(axis=1)
        self.df["Score E"] = self.df[fields_percentile].mean(axis=1)

        # Calculate correlations
        self.df[fields_min_max].corr()

        # Create percentiles for the scores
        for score_field in [
            "Score A",
            "Score B",
            "Score C",
            "Score D",
            "Score E",
            "Poverty (Less than 200% of federal poverty line)",
        ]:
            self.df[f"{score_field}{self.PERCENTILE_FIELD_SUFFIX}"] = self.df[score_field].rank(pct=True)

            for threshold in [0.25, 0.3, 0.35, 0.4]:
                fraction_converted_to_percent = int(100 * threshold)
                self.df[f"{score_field} (top {fraction_converted_to_percent}th percentile)"] = (
                        self.df[f"{score_field}{self.PERCENTILE_FIELD_SUFFIX}"] >= 1 - threshold
                )

    def load(self) -> None:
        logger.info(f"Saving Score CSV")

        # write nationwide csv
        self.SCORE_CSV_PATH.mkdir(parents=True, exist_ok=True)
        self.df.to_csv(self.SCORE_CSV_PATH / f"usa.csv", index=False)
