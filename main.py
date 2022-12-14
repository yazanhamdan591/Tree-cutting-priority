from typing import List, Union, Tuple

from osgeo import gdal
import argparse
import numpy as np

from utils.shape_files_directory_handler import ShapeFilesDirectoryHandler

NO_DATA_VALUE = -9999
RASTER_WIDTH = 230
RASTER_HEIGHT = 221


def get_classification_ranges(
        min_val: Union[float, int],
        max_val: Union[float, int]
) -> List[Tuple[float, float]]:
    """ Splits values into ranges to classify into 9 categories.

    Args:
        min_val: [number] Minimum values of the data.
        max_val: [number] Maximum value of the data.

    Returns:
        List[Tuple[float, float]]: The ranges to classify with.
    """
    data_range = (max_val - min_val) / 9
    return [(min_val + (data_range * i), min_val + (data_range * (i + 1)))
            for i in range(0, 9)]


def classify_band(band: gdal.Band, reverse=False) -> None:
    """Classify raster band values.

    Args:
        band (gdal.Band): The raster band to classify.
        reverse (bool, optional): Whether to reverse classification values.
        Defaults to False.
    """
    [data_min, data_max, _, __] = band.GetStatistics(True, True)

    band_data = np.array(band.ReadAsArray())

    new_band_data = classify_arr(band_data, reverse, data_min, data_max)

    band.WriteArray(new_band_data)


def classify_arr(arr: np.ndarray, reverse: bool,
                 min: int, max: int) -> np.ndarray:
    """Classify arr values.

    Args:
        arr (gdal.Band): The array band to classify.
        reverse (bool, optional): Whether to reverse classification values.
        Defaults to False.
        min: (int): The lower bound of the resulting array
        max: (int): The upper bound of the resulting array
    """
    band_data = arr
    final_data = band_data.copy()
    ranges = get_classification_ranges(min, max)
    current_class = 1 if not reverse else 9

    for val_range in ranges:
        data_selection = \
            (final_data >= val_range[0]) & (final_data < val_range[1]) \
            if val_range[0] != ranges[-1][0] \
            else (final_data >= val_range[0]) & (final_data <= val_range[1])

        final_data[data_selection] = current_class

        if reverse:
            current_class -= 1
        else:
            current_class += 1

    return final_data


def rasterize_shapefile(shape_file: gdal.Dataset, raster_file_name: str,
                        **args) -> gdal.Dataset:
    """ Rasterize vector shape file.

        Args:
            shape_file: The shape file to rasterize.
            raster_file_name: The output filename of the raster.
            args: Extra arguments to the rasterization method.

        Returns:
            gdal.Dataset: The raster as a GDAL Dataset.
    """
    pixel_size = 25
    x_min = 522556.47860572586
    y_max = 3786279.2744338084

    source_layer: gdal.Dataset = shape_file.GetLayer()

    target_ds: gdal.Dataset = gdal.GetDriverByName('GTiff').Create(
        raster_file_name, RASTER_WIDTH, RASTER_HEIGHT, 1, gdal.GDT_Float32)
    target_ds.SetGeoTransform((x_min, pixel_size, 0, y_max, 0, -pixel_size))
    target_ds.SetProjection(source_layer.GetSpatialRef().ExportToWkt())
    band: gdal.Band = target_ds.GetRasterBand(1)
    band.SetNoDataValue(NO_DATA_VALUE)

    # Rasterize
    gdal.RasterizeLayer(target_ds, [1], source_layer, **args)

    return target_ds


def calculate_raster_distance(target_ds: gdal.Dataset):
    """ Calculate Euclidean Distance for the raster.

        Args:
            target_ds: The created GeoTiff file.
    """
    band = target_ds.GetRasterBand(1)
    gdal.ComputeProximity(band, band,
                          options=['VALUES=0', 'DISTUNITS=GEO', 'MAXDIST=100'])
    classify_band(band, True)


def split_size_into(size: int, split_into=5) -> List[Tuple[int]]:
    curr_min = 0
    sizes: List = []

    for split in range(1, split_into + 1):
        min_to_use = curr_min
        max_to_use = int((split / split_into) *
                         size) if split != split_into else size

        sizes.append((min_to_use, max_to_use))
        curr_min = max_to_use

    return sizes


def blockify_matrix(mat: np.ndarray) -> Tuple[List[Tuple[int]]]:
    (x, y) = mat.shape

    return (split_size_into(x), split_size_into(y))


def zonal_avg(mat: np.ndarray) -> np.ndarray:
    (xs, ys) = blockify_matrix(output_raster)
    zonal_mat = mat.copy()

    for x in xs:
        for y in ys:
            temp = mat[x[0]: x[1], y[0]: y[1]]
            zonal_mat[x[0]: x[1], y[0]: y[1]] = np.average(temp)

    return zonal_mat


def save_arr_as_raster(
    name: str,
    geo_transform: Tuple[float, float, float, float, float, float],
    projection: str,
    arr: np.ndarray
) -> None:
    end_ds: gdal.Dataset = gdal.GetDriverByName('GTiff').Create(
        name, RASTER_WIDTH, RASTER_HEIGHT, 1, gdal.GDT_Float32)

    end_ds.SetGeoTransform(geo_transform)
    end_ds.SetProjection(projection)
    end_band: gdal.Band = end_ds.GetRasterBand(1)
    end_band.SetNoDataValue(NO_DATA_VALUE)
    end_band.WriteArray(arr, 0, 0)
    end_band.FlushCache()


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("-sp", "--shape_path",
                            help="The absolute path to the shape files.",
                            required=True)

    cmd_args = arg_parser.parse_args()

    file_handler = ShapeFilesDirectoryHandler(cmd_args.shape_path)
    shape_files = file_handler.read_shapefiles()

    wanted_features = {
        "EgressRoutes": {"weight": 0.2, "column_to_use": None},
        "Communityfeatures": {"weight": 0.3, "column_to_use": None},
        "DistCircuits": {"weight": 0.1, "column_to_use": None},
        "PopulatedAreast": {"weight": 0.3, "column_to_use": "pop_per_sq"},
        "SBNFMortalityt": {"weight": 0.1, "column_to_use": "tot_mortal"},
    }

    output_raster: np.ndarray = None
    geo_transform: Tuple[float, float, float, float, float, float] = None
    projection: str = None

    for feature, details in wanted_features.items():
        feature_shape_file = shape_files[feature]

        if details["column_to_use"] is not None:
            extra_params = {
                "options": [f'ATTRIBUTE={details["column_to_use"]}']
            }
            ds = rasterize_shapefile(feature_shape_file, f"{feature}.tiff",
                                     **extra_params)
            classify_band(ds.GetRasterBand(1))
        else:
            ds = rasterize_shapefile(
                feature_shape_file, f"{feature}.tiff", burn_values=[0])
            calculate_raster_distance(ds)

        if geo_transform is None:
            geo_transform = ds.GetGeoTransform()

        if projection is None:
            projection = ds.GetProjection()

        band_as_arr: np.ndarray = np.array(ds.GetRasterBand(1).ReadAsArray())
        band_as_arr[band_as_arr == NO_DATA_VALUE] = 1

        if output_raster is not None:
            output_raster += band_as_arr * details["weight"]
        else:
            output_raster = band_as_arr * details["weight"]

    save_arr_as_raster("final.tiff", geo_transform, projection, output_raster)

    zonal_data = zonal_avg(output_raster)

    save_arr_as_raster("zonal_avg.tiff", geo_transform, projection, zonal_data)
    save_arr_as_raster("zonal_avg_classified.tiff",
                       geo_transform, projection,
                       classify_arr(zonal_data, False,
                                    zonal_data.min(), zonal_data.max()))
