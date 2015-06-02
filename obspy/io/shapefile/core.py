# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
from future.builtins import *  # NOQA
from future.utils import native_str

from obspy import Catalog
from obspy.core.event import Origin, Magnitude
from obspy.core.inventory import Inventory

try:
    import gdal
    from osgeo import ogr, osr
except ImportError as e:
    has_GDAL = False
    IMPORTERROR_MSG = str(e) + (
        ". ObsPy's write support for shapefiles requires the 'gdal' module "
        "to be installed in addition to the general ObsPy dependencies.")
else:
    has_GDAL = True
    gdal.UseExceptions()


WGS84_WKT = \
    """
    GEOGCS["WGS 84",
        DATUM["WGS_1984",
            SPHEROID["WGS 84",6378137,298.257223563,
                AUTHORITY["EPSG","7030"]],
            AUTHORITY["EPSG","6326"]],
        PRIMEM["Greenwich",0,
            AUTHORITY["EPSG","8901"]],
        UNIT["degree",0.0174532925199433,
            AUTHORITY["EPSG","9122"]],
        AUTHORITY["EPSG","4326"]]
    """


def _write_shapefile(obj, filename, **kwargs):
    """
    Write :class:`~obspy.core.event.Catalog` object to a ESRI shapefile.

    :type obj: :class:`~obspy.core.event.Catalog` or
        :class:`~obspy.core.inventory.Inventory`
    :param obj: ObsPy object for shapefile output
    :type filename: str or file
    :param filename: Filename to write to. According to ESRI shapefile
        definition, multiple files with the following suffixes will be written:
        ".shp", ".shx", ".dbj", ".prj".
    """
    if not has_GDAL:
        raise ImportError(IMPORTERROR_MSG)
    if not filename.endswith(".shp"):
        filename += ".shp"

    driver = ogr.GetDriverByName(native_str("ESRI Shapefile"))

    driver.DeleteDataSource(filename)
    data_source = driver.CreateDataSource(filename)

    # create the layer
    if isinstance(obj, Catalog):
        _add_catalog_layer(data_source, obj)
    elif isinstance(obj, Inventory):
        _add_inventory_layer(data_source, obj)
    else:
        msg = ("Object for shapefile output must be a Catalog or Inventory.")
        raise TypeError(msg)


def _add_catalog_layer(data_source, catalog):
    """
    :type data_source: :class:`osgeo.ogr.DataSource`.
    :param data_source: OGR data source the layer is added to.
    :type catalog: :class:`~obspy.core.event.Catalog`
    :param catalog: Event data to add as a new layer.
    """
    if not has_GDAL:
        raise ImportError(IMPORTERROR_MSG)
    # create the spatial reference
    sr = osr.SpatialReference()
    # Simpler and feels cleaner to initialize by EPSG code but that depends on
    # a csv file shipping with GDAL and some GDAL environment paths being set
    # correctly which was not the case out of the box in anaconda, so better
    # hardcode this bit.
    # sr.ImportFromEPSG(4326)
    sr.ImportFromWkt(WGS84_WKT)

    layer = data_source.CreateLayer(native_str("earthquakes"), sr,
                                    ogr.wkbPoint)

    # Add the fields we're interested in (10 char max)
    for name in ["EventID", "OriginID", "Magnitu_ID"]:
        field = ogr.FieldDefn(native_str(name), ogr.OFTString)
        field.SetWidth(100)
        layer.CreateField(field)
    # ESRI shapefile attributes are stored in dbf files, which can not
    # store datetimes, only dates, see:
    # http://www.gdal.org/drv_shapefile.html
    field = ogr.FieldDefn(native_str("Date"), ogr.OFTDate)
    layer.CreateField(field)
    # use POSIX timestamp for exact origin time, set time of first pick
    # for events with no origin
    for name in ["OriginTime", "FirstPick"]:
        field = ogr.FieldDefn(native_str(name), ogr.OFTReal)
        field.SetWidth(20)
        field.SetPrecision(6)
        layer.CreateField(field)
    for name in ["Latitude", "Longitude"]:
        field = ogr.FieldDefn(native_str(name), ogr.OFTReal)
        field.SetWidth(16)
        field.SetPrecision(10)
        layer.CreateField(field)
    for name in ["Depth", "Magnitude"]:
        field = ogr.FieldDefn(native_str(name), ogr.OFTReal)
        field.SetWidth(8)
        field.SetPrecision(3)
        layer.CreateField(field)

    layer_definition = layer.GetLayerDefn()
    for event in catalog:
        # try to use preferred origin/magnitude, fall back to first or use
        # empty one with `None` values in it
        origin = (event.preferred_origin() or
                  event.origins and event.origins[0] or
                  Origin(force_resource_id=False))
        magnitude = (event.preferred_magnitude() or
                     event.magnitudes and event.magnitudes[0] or
                     Magnitude(force_resource_id=False))
        t_origin = origin.time
        pick_times = [pick.time for pick in event.picks
                      if pick.time is not None]
        t_pick = pick_times and min(pick_times) or None
        date = t_origin or t_pick

        feature = ogr.Feature(layer_definition)

        # setting fields with `None` results in values of `0.000`
        # need to really omit setting values if they are `None`
        if event.resource_id is not None:
            feature.SetField(native_str("EventID"),
                             native_str(event.resource_id))
        if origin.resource_id is not None:
            feature.SetField(native_str("OriginID"),
                             native_str(origin.resource_id))
        if t_origin is not None:
            # Use timestamp for exact timing
            feature.SetField(native_str("OriginTime"), t_origin.timestamp)
        if t_pick is not None:
            # Use timestamp for exact timing
            feature.SetField(native_str("FirstPick"), t_pick.timestamp)
        if date is not None:
            # ESRI shapefile attributes are stored in dbf files, which can not
            # store datetimes, only dates.
            # We still need to use the GDAL API with precision up to seconds
            # (aiming at other output drivers of GDAL; `100` stands for GMT)
            feature.SetField(native_str("Date"), date.year, date.month,
                             date.day, date.hour, date.minute, date.second,
                             100)
        if origin.latitude is not None:
            feature.SetField(native_str("Latitude"), origin.latitude)
        if origin.longitude is not None:
            feature.SetField(native_str("Longitude"), origin.longitude)
        if origin.depth is not None:
            feature.SetField(native_str("Depth"), origin.depth / 1e3)
        if magnitude.mag is not None:
            feature.SetField(native_str("Magnitude"), magnitude.mag)
        if magnitude.resource_id is not None:
            feature.SetField(native_str("Magnitu_ID"),
                             native_str(magnitude.resource_id))

        if origin.latitude is not None and origin.longitude is not None:
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint(origin.longitude, origin.latitude)
            feature.SetGeometry(point)

        layer.CreateFeature(feature)
        # Destroy the feature to free resources
        feature.Destroy()

    # Destroy the data source to free resources
    data_source.Destroy()


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
