from typing import Dict, Tuple, Optional, Union, Any
import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
import math
import time
import os
from pathlib import Path
import webbrowser
import threading
import reverse_geocode
import pycountry
import subprocess
from dataclasses import dataclass
from typing import List, Callable
from collections import defaultdict
import logging
from timezonefinder import TimezoneFinder
from sklearn.metrics import r2_score, root_mean_squared_error
import datetime
from datetime import timedelta

from .tapas import TAPAS
from .trips_pb2 import Paths

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_EPSILON = 1e-9


class DateRangeHandler:
    """Handles date range processing for both traveltime and transit modules"""
    
    @staticmethod
    def parse_date_range(date_range_str: list) -> list:
        """Parse date range string into list of dates
        
        Args:
            date_range_str: List containing either:
                - Single element: number of days from today (e.g., ['5'])
                - Two elements: start and end dates (e.g., ['2024-01-01', '2024-01-05'])
        
        Returns:
            List of date strings in 'YYYY-MM-DD' format
        """
        if not date_range_str:
            return []
        
        try:
            # Case 1: Single number (days from today)
            if len(date_range_str) == 1:
                days_input = date_range_str[0]
                
                # Try to parse as integer (number of days)
                try:
                    num_days = int(days_input)
                    if num_days <= 0:
                        logger.error(f"Number of days must be positive: {num_days}")
                        return []
                    
                    # Generate dates from today for num_days
                    today = datetime.datetime.now()
                    dates = []
                    for i in range(num_days):
                        date_obj = today + timedelta(days=i)
                        dates.append(date_obj.strftime('%Y-%m-%d'))
                    
                    logger.info(f"Generated {num_days} days from today: {dates}")
                    return dates
                    
                except ValueError:
                    # Try to parse as single date
                    try:
                        single_date = datetime.datetime.strptime(days_input, '%Y-%m-%d')
                        result = [single_date.strftime('%Y-%m-%d')]
                        logger.info(f"Using single date: {result}")
                        return result
                    except ValueError:
                        logger.error(f"Invalid date format: {days_input}")
                        return []
            
            # Case 2: Two dates (start and end)
            elif len(date_range_str) == 2:
                start_date = datetime.datetime.strptime(date_range_str[0], '%Y-%m-%d')
                end_date = datetime.datetime.strptime(date_range_str[1], '%Y-%m-%d')
                
                if start_date > end_date:
                    logger.error(f"Start date {date_range_str[0]} is after end date {date_range_str[1]}")
                    return []
                
                dates = []
                current_date = start_date
                while current_date <= end_date:
                    dates.append(current_date.strftime('%Y-%m-%d'))
                    current_date += timedelta(days=1)
                
                logger.info(f"Generated date range from {date_range_str[0]} to {date_range_str[1]}: {dates}")
                return dates
            
            else:
                logger.error(f"Invalid date range format: {date_range_str}. Expected 1 or 2 elements.")
                return []
                
        except ValueError as e:
            logger.error(f"Invalid date format in range: {e}")
            return []
    
    @staticmethod
    def should_process_date_range(date_range: list) -> bool:
        """Check if date range should be processed"""
        return date_range and len(date_range) > 0


class HoursHandler:
    """Handles hours validation and processing for both traveltime and transit modules"""
    
    @staticmethod
    def validate_hours(hours: List[Union[str, int]]) -> List[Union[str, int]]:
        """Validate and process hours input
        
        Args:
            hours: List of hours as strings or integers
            
        Returns:
            List of validated hours (integers 0-23, or 'now')
            
        Raises:
            ValueError: If hours format is invalid
        """
        logger.debug(f"Validating hours input: {hours}")
        if not hours:
            logger.error("Hours list is empty.")
            raise ValueError("Hours list cannot be empty.")

        if 'now' in hours:
            if len(hours) > 1:
                logger.warning("'now' specified in hours list; ignoring other values.")
            return ['now']
        if 'all' in hours:
            if len(hours) > 1:
                logger.warning("'all' specified in hours list; ignoring other values.")
            return list(range(24))  # Return integers 0-23

        validated_hours = []
        for hour in hours:
            try:
                hour_int = int(hour)
                if not 0 <= hour_int <= 23:
                    raise ValueError(f"Invalid hour '{hour}'. Hours must be between 0 and 23.")
                validated_hours.append(hour_int)  # Store as integer
            except (ValueError, TypeError):
                raise ValueError(f"Invalid hour format '{hour}'. Hours must be integers between 0 and 23, 'now', or 'all'.")

        logger.debug(f"Validated hours: {validated_hours}")
        return validated_hours


@dataclass
class NetworkConfig:
    """Configuration for network preparation."""
    network_path: Path
    centroids_path: Path
    link_tt_path: Optional[Path] = None
    od_demands_path: Optional[Path] = None
    cap_factor_a: Optional[float] = 0.6
    cap_factor_f: Optional[float] = 0.7
    fftt_factor: float = 1.0
    demand_factor: Optional[float] = 1.0


@dataclass
class AssignmentConfig:
    """Configuration for traffic assignment."""
    algorithm: str
    cost_function: Callable
    accuracy: float
    max_iter: int
    verbose: bool
    proportions: bool = False


class NetworkUtils:
    """Core network utility functions."""

    @staticmethod
    def prepare_graph(config: NetworkConfig) -> Tuple[nx.DiGraph, Dict, Dict, Union[Dict, List], bool]:
        """Prepare network graph with attributes."""
        logger.info("Preparing network graph...")
        logger.debug(f"Network config: {config}")
        try:
            logger.debug(f"Reading centroids from: {config.centroids_path}")
            centroids = gpd.read_file(config.centroids_path)
            logger.debug(f"Reading network CSV from: {config.network_path}")
            network = pd.read_csv(config.network_path, header=0)
            link_index_ordering = list(zip(network['from'], network['to']))
            logger.debug(f"Created link index ordering with {len(link_index_ordering)} links.")

            net_graph = nx.DiGraph()
            logger.debug("Creating initial edge attributes...")
            edge_attr = NetworkUtils._create_edge_attributes(
                network,
                config.cap_factor_a,
                config.cap_factor_f,
                config.fftt_factor
            )

            # Create edges list
            logger.debug("Creating initial edges list...")
            edges = NetworkUtils._create_edges_list(network)

            # Create centroid mappings
            logger.debug("Creating centroid mappings...")
            max_ft = max(network['from'].max(), network['to'].max())
            from_cent = {
                row['ZoneID']: max_ft + index + 1
                for index, row in centroids.iterrows()
            }
            max_ft = max(from_cent.values())
            to_cent = {
                row['ZoneID']: max_ft + index + 1
                for index, row in centroids.iterrows()
            }
            logger.debug("Centroid mappings created.")

            mlt = False
            if config.link_tt_path:
                logger.info(f"Processing link travel time file: {config.link_tt_path}")
                edge_attr, mlt = NetworkUtils._process_link_tt(
                    config.link_tt_path,
                    edges,
                    edge_attr
                )
                logger.info(f"Link TT processing complete. Measured Link Travel Time (MLT) present: {mlt}")

            # Add centroid connections
            logger.debug("Adding centroid connections...")
            edges, edge_attr = NetworkUtils._add_centroid_connections(
                edges,
                edge_attr,
                centroids,
                from_cent,
                to_cent
            )
            logger.debug("Centroid connections added.")

            # Create graph
            logger.debug("Adding edges to graph...")
            net_graph.add_edges_from(edges)
            logger.debug("Setting edge attributes...")
            nx.set_edge_attributes(net_graph, edge_attr)
            logger.info(f"Graph created with {net_graph.number_of_nodes()} nodes and {net_graph.number_of_edges()} edges.")

            if config.od_demands_path:
                logger.info(f"Reading OD demands from: {config.od_demands_path}")
                demands = NetworkUtils.read_od(
                    config.od_demands_path,
                    config.demand_factor
                )
                logger.info(f"Loaded {len(demands)} OD pairs.")
                return net_graph, from_cent, to_cent, demands, link_index_ordering

            return net_graph, from_cent, to_cent, link_index_ordering, mlt

        except FileNotFoundError as e:
            logger.error(f"File not found during graph preparation: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.exception(f"Error during graph preparation: {e}")
            raise

    @staticmethod
    def _create_edge_attributes(network: pd.DataFrame,
                                cap_factor_a: float,
                                cap_factor_f: float,
                                fftt_factor: float) -> Dict:
        """Create edge attributes dictionary."""
        logger.debug("Starting _create_edge_attributes...")
        edge_attr = {}
        for _, row in network.iterrows():
            # Calculate capacity based on road type
            if row['etype'] == 'Arterial':
                capacity = row['capacity'] * cap_factor_a
            elif row['etype'] == 'Freeway':
                capacity = row['capacity'] * cap_factor_f
            else:
                capacity = row['capacity']

            fft = row['fft'] * fftt_factor

            edge_attr[(row['from'], row['to'])] = {
                'capacity': capacity,
                'length': row['length'],
                'FFT': fft,
                'alpha': row['alpha'],
                'beta': row['beta'],
                'etype': row['etype'],
                'flow': 0,
                'cost': fft,
                'dcost': 0,
                'Oflow': None,
                'Oflowid': None,
                'Obasedflow': None,
                'cost_derivative': 0
            }

        logger.debug(f"Finished _create_edge_attributes. Created attributes for {len(edge_attr)} edges.")
        return edge_attr

    @staticmethod
    def _create_edges_list(network: pd.DataFrame) -> List[Tuple[int, int]]:
        """Create list of edges from network data."""
        logger.debug("Starting _create_edges_list...")
        edges = [(row[1], row[2]) for row in network.itertuples()]
        logger.debug(f"Finished _create_edges_list. Created {len(edges)} edges.")
        return edges

    @staticmethod
    def _process_link_tt(link_tt_path: Path,
                        edges: List[Tuple[int, int]],
                        edge_attr: Dict) -> Tuple[Dict, bool]:
        """Process link travel time data."""
        logger.debug(f"Starting _process_link_tt for {link_tt_path}...")
        link_tt = pd.read_csv(link_tt_path, header=0)
        tt_zip = list(zip(link_tt['from'], link_tt['to']))
        logger.debug(f"Loaded {len(link_tt)} rows from link TT file.")

        # Validate edges
        missing_edges = [e for e in edges if e not in tt_zip]
        if missing_edges:
            logger.warning(f'Some edges missing in linkTT file ({link_tt_path}): {len(missing_edges)} edges. Example: {missing_edges[:5]}')
            raise ValueError(f"Edges missing in linkTT file: {missing_edges}")

        mlt = False
        try:
            if 'observed_id' in link_tt.columns and not link_tt['observed_id'].isnull().all():
                max_oflow_id = max(link_tt['observed_id'].dropna().max(), 0)
            else:
                max_oflow_id = 0
        except KeyError:
            max_oflow_id = 0
            logger.debug("'observed_id' column not found in link TT file.")

        if 'observed_id' not in link_tt.columns:
            link_tt['observed_id'] = None
            logger.debug("Added missing 'observed_id' column, initialized to None.")

        processed_count = 0
        for row in link_tt.itertuples(index=False):
            edge = (row[0], row[1])
            if edge in edge_attr:
                max_oflow_id += 1
                edge_attr[edge]['OTT'] = row[3]
                edge_attr[edge]['Oflow'] = row[2]
                edge_attr[edge]['Oflowid'] = max_oflow_id if row[8] is None else getattr(row, 'observed_id', None)
                if not mlt and edge_attr[edge]['Oflow'] is not None and edge_attr[edge]['Oflow'] > 0:
                    mlt = True
                processed_count += 1
            else:
                logger.warning(f"Edge {edge} found in link TT file but not in network attributes.")

        logger.debug(f"Finished _process_link_tt. Processed {processed_count} edges. MLT: {mlt}")
        return edge_attr, mlt

    @staticmethod
    def _add_centroid_connections(edges: List[Tuple[int, int]],
                                edge_attr: Dict,
                                centroids: gpd.GeoDataFrame,
                                from_cent: Dict,
                                to_cent: Dict) -> Tuple[List[Tuple[int, int]], Dict]:
        """Add centroid connections to network."""
        logger.debug("Starting _add_centroid_connections...")
        new_edges = []

        # Create origin connections
        origin_connections = [
            (from_cent[row['ZoneID']], int(row['NodeID']))
            for _, row in centroids.iterrows()
        ]
        new_edges.extend(origin_connections)
        logger.debug(f"Created {len(origin_connections)} origin centroid connections.")

        # Create destination connections
        dest_connections = [
            (int(row['NodeID']), to_cent[row['ZoneID']])
            for _, row in centroids.iterrows()
        ]
        new_edges.extend(dest_connections)
        logger.debug(f"Created {len(dest_connections)} destination centroid connections.")

        # Add edges and attributes
        edges.extend(new_edges)
        edge_attr.update({
            edge: {
                'capacity': 100000,
                'length': 0,
                'FFT': 0,
                'alpha': 0.15,
                'beta': 4,
                'etype': 'Centroid',
                'flow': 0,
                'cost': 0,
                'dcost': 0,
                'Oflow': None,
                'Oflowid': None,
                'OTT': 0,
                'Obasedflow': None,
                'cost_derivative': 0
            }
            for edge in new_edges
        })
        logger.debug(f"Added attributes for {len(new_edges)} centroid connector edges.")
        logger.debug("Finished _add_centroid_connections.")
        return edges, edge_attr

    @staticmethod
    def read_od(od_path: Union[str, Path],
                demand_factor: float = 1.0) -> Dict[Tuple[int, int], float]:
        """Read OD matrix from file."""
        logger.debug(f"Reading OD matrix from {od_path} with demand factor {demand_factor}...")
        try:
            od_demands_df = pd.read_csv(od_path, header=0)
            demands = {
                (row['Origin'], row['Destination']): row['Demand'] * demand_factor
                for _, row in od_demands_df.iterrows()
            }
            logger.debug(f"Read {len(demands)} OD pairs.")
            return demands
        except FileNotFoundError:
            logger.error(f"OD file not found: {od_path}", exc_info=True)
            raise
        except Exception as e:
            logger.exception(f"Error reading OD file {od_path}: {e}")
            raise


class AssignmentCalculator:
    """Handles travel time and path calculations."""

    @staticmethod
    def get_fftt(graph: nx.DiGraph, path: List[int]) -> float:
        """Calculate free flow travel time for a path."""
        return sum(
            graph.edges[path[i], path[i+1]]['FFT']
            for i in range(len(path)-1)
        )

    @staticmethod
    def get_dist(graph: nx.DiGraph, path: List[int]) -> float:
        """Calculate total distance for a path."""
        return sum(
            graph.edges[path[i], path[i+1]]['length']
            for i in range(len(path)-1)
        )
    
    @staticmethod
    def get_oodtt(graph: nx.DiGraph, path: List[int]) -> float:
        """Calculate observed travel time for a path."""
        return sum(
            graph.edges[path[i], path[i+1]]['OTT']
            for i in range(len(path)-1)
        )

    @staticmethod
    def haversine(lon1: float,
                lat1: float,
                lon2: float,
                lat2: float,
                unit: str = 'meter') -> float:
        """Calculate haversine distance between points."""
        # Convert to radians
        lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])

        # Calculate differences
        dlon = lon2 - lon1
        dlat = lat2 - lat1

        # Haversine formula
        a = (np.sin(dlat/2)**2 + np.cos(lat1) *
             np.cos(lat2) * np.sin(dlon/2)**2)
        c = 2 * np.arcsin(np.sqrt(a))

        # Earth radius in meters
        r = 6371000

        # Convert to requested unit
        dist = c * r
        if unit == 'meter':
            return dist
        elif unit == 'km':
            return dist / 1000
        elif unit == 'miles':
            return dist / 1609.34
        else:
            raise NotImplementedError(f'Unit not supported: {unit}')


class TrafficAssignment:
    """
    Handles the traffic assignment process using various algorithms like
    Frank-Wolfe (FW), standard Conjugate Frank-Wolfe (CFW), standard
    Bi-conjugate Frank-Wolfe (BFW), Method of Successive Averages (MSA),
    and TAPAS.

    Includes optimizations for proportion tracking.
    """

    def __init__(self, config: Any):  # Use Any if AssignmentConfig structure isn't fixed
        """
        Initializes the TrafficAssignment class.

        Args:
            config: An object or dictionary containing assignment parameters like
                    algorithm, max_iter, accuracy, cost_function, proportions, verbose.
                    (Ideally, use a dataclass like AssignmentConfig).
        """
        logger.debug(f"Initializing TrafficAssignment with config: {config}")
        self.config = config
        # Set a threshold slightly higher than target accuracy to start proportion tracking early
        self.proportion_tracking_threshold = self.config.accuracy * 1.2
        self.epsilon = DEFAULT_EPSILON  # Epsilon for numerical stability checks
        logger.debug(
            f"Proportion tracking threshold set to: {self.proportion_tracking_threshold}")
        logger.debug(f"Numerical stability epsilon set to: {self.epsilon}")

        # Validate config
        if not hasattr(self.config, 'cost_function') or not callable(self.config.cost_function):
            raise ValueError(
                "Configuration must include a valid 'cost_function'")
        if not hasattr(self.config, 'algorithm'):
            raise ValueError("Configuration must include 'algorithm'")
        supported_algorithms = ['FW', 'CFW', 'BFW', 'MSA', 'TAPAS']
        if self.config.algorithm not in supported_algorithms:
            raise NotImplementedError(
                f"Algorithm '{self.config.algorithm}' is not supported. "
                f"Use one of: {', '.join(supported_algorithms)}"
            )
        if not hasattr(self.config, 'max_iter'):
            raise ValueError("Configuration must include 'max_iter'")
        if not hasattr(self.config, 'accuracy'):
            raise ValueError("Configuration must include 'accuracy'")
        if not hasattr(self.config, 'proportions'):
            self.config.proportions = False  # Default proportions to False if not provided
        if not hasattr(self.config, 'verbose'):
            self.config.verbose = True  # Default verbose to True if not provided


    def assign_traffic(self,
                    graph: nx.DiGraph,
                    from_cent: Dict[Any, Any],
                    to_cent: Dict[Any, Any],
                    demands: Dict[Tuple[Any, Any], float]
                    ) -> Tuple[nx.DiGraph, float, Dict, Dict, Optional[Dict]]:
        """
        Executes the traffic assignment process based on the configured algorithm.

        Args:
            graph: The network graph (NetworkX DiGraph). Edges must have attributes
                compatible with the cost_function (e.g., 'capacity', 'free_flow_time')
                and will be updated with 'flow', 'cost', 'dcost'.
            from_cent: Dictionary mapping origin zone IDs to network node IDs.
            to_cent: Dictionary mapping destination zone IDs to network node IDs.
            demands: Dictionary mapping (origin_zone, destination_zone) tuples to demand values.

        Returns:
            A tuple containing:
            - graph: The updated graph with final flows and costs.
            - tstt: The final Total System Travel Time.
            - sp_edges: Dictionary mapping OD pairs to their shortest path edge lists (if available).
            - eodtt: Dictionary mapping OD pairs to their end-of-iteration travel times (if available).
            - od_link_prop: Dictionary mapping edges to OD flow proportions (if proportions enabled).
                            Structure: {edge: {(o, d): flow, ...}, ...} Returns the final proportions.
        """
        # set logger level to not even print info
        if self.config.verbose:
            logger.setLevel(logging.INFO)
        else:
            logger.setLevel(logging.WARNING)
        logger.info(
            f"Starting traffic assignment using algorithm: {self.config.algorithm}")
        gap = np.inf
        tstt = np.inf
        iterations = 0
        start_time = time.perf_counter()
        threshold_hit = False
        od_link_prop_current = None
        od_link_prop_accumulated = None  # Proportions accumulated based on flow updates

        # --- TAPAS Algorithm Handling ---
        if self.config.algorithm == 'TAPAS':
            logger.info("Using TAPAS algorithm...")
            try:
                result = self._assign_tapas(graph, from_cent, to_cent, demands, start_time)
                if self.config.proportions:
                    graph_t, tstt_t, sp_edges_t, eodtt_t, od_link_prop_t = result
                    return graph_t, tstt_t, sp_edges_t, eodtt_t, od_link_prop_t
                else:
                    graph_t, tstt_t, sp_edges_t, eodtt_t = result
                    return graph_t, tstt_t, sp_edges_t, eodtt_t
            except ImportError:
                logger.error(
                    "TAPAS algorithm selected but 'tapas' module not found or importable.")
                raise
            except Exception as e:
                logger.error(
                    f"Error during TAPAS execution: {e}", exc_info=True)
                raise

        # --- Iterative Algorithms (FW, MSA, CFW, BFW) ---
        logger.debug("Initializing flows for iterative assignment...")
        # Initialize flows (x_k). Use existing 'flow' if available, else 0.
        x_k = {edge: data.get('flow', 0.0)
                            for edge, data in graph.edges.items()}
        # Ensure graph reflects initial state and calculate initial costs/derivatives
        for u, v, data in graph.edges(data=True):
            flow = x_k.get((u, v), 0.0)
            graph.edges[u, v]['flow'] = flow
            try:
                cost, dcost = self.config.cost_function(data, flow)
                graph.edges[u, v]['cost'] = cost
                graph.edges[u, v]['dcost'] = dcost
            except Exception as e:
                logger.error(
                    f"Error in cost function during initialization for edge {(u, v)} with flow {flow}: {e}", exc_info=True)
                raise ValueError(
                    f"Cost function failed during initialization for edge {(u, v)}")

        # Check if graph has zero flow initially. If so, perform one AON load.
        if all(abs(flow) < self.epsilon for flow in x_k.values()):
            logger.info(
                "Initial flows are zero. Performing initial AON loading to get feasible solution.")
            _, y_0, _, _ = self._aon_loading(
                graph, from_cent, to_cent, demands, compute_sptt=False, track_proportions=False
            )
            x_k = y_0.copy()  # Set initial flow x_0 = y_0
            for u, v, data in graph.edges(data=True):
                flow = x_k.get((u, v), 0.0)
                data['flow'] = flow
                try:
                    data['cost'], data['dcost'] = self.config.cost_function(
                        data, flow)
                except Exception as e:
                    logger.error(
                        f"Error in cost function after initial AON for edge {(u, v)} with flow {flow}: {e}", exc_info=True)
                    raise ValueError(
                        f"Cost function failed after initial AON for edge {(u, v)}")
            logger.debug("Initial flow set based on AON loading.")
            tstt = self._calculate_tstt(graph)  # Calculate initial TSTT
        else:
            logger.info("Using existing flows as starting point.")
            for u, v, data in graph.edges(data=True):
                if 'cost' not in data or 'dcost' not in data:
                    try:
                        data['cost'], data['dcost'] = self.config.cost_function(
                            data, data['flow'])
                    except Exception as e:
                        logger.error(
                            f"Error in cost function for existing flow on edge {(u, v)}: {e}", exc_info=True)
                        raise ValueError(
                            f"Cost function failed for existing flow on edge {(u, v)}")
            tstt = self._calculate_tstt(graph)  # Calculate initial TSTT

        # Initialize search direction storage for CFW/BFW
        d_k_prev = None  # Previous search direction d_{k-1}
        d_k_prev2 = None  # Search direction d_{k-2} (for BFW)
        sptt = None
        logger.info("Starting assignment iterations...")
        while gap > self.config.accuracy:
            iterations += 1
            if iterations > self.config.max_iter:
                self._log_progress(iterations - 1, gap, tstt, sptt,
                                start_time, max_iter_reached=True)
                break

            # --- 1. Direction Finding (AON Loading) ---
            logger.debug(f"Iteration {iterations}: Performing AON loading...")
            should_track_aon_proportions = self.config.proportions and threshold_hit
            if should_track_aon_proportions:
                logger.debug(
                    f"Iteration {iterations}: AON loading with proportion tracking.")
                sptt, y_k, sp_edges, eodtt, od_link_prop_current = self._aon_loading(
                    graph, from_cent, to_cent, demands, compute_sptt=True, track_proportions=True
                )
                if od_link_prop_accumulated is None:
                    logger.debug(
                        "Initializing accumulated proportions based on current AON proportions.")
                    od_link_prop_accumulated = od_link_prop_current.copy()
            else:
                logger.debug(f"Iteration {iterations}: AON loading without proportion tracking.")
                sptt, y_k, sp_edges, eodtt = self._aon_loading(
                    graph, from_cent, to_cent, demands, compute_sptt=True, track_proportions=False
                )
                od_link_prop_current = None # Ensure it's None if not tracked
            logger.debug(f"Iteration {iterations}: Calculated SPTT = {sptt:.5f}")

            # --- 2. Calculate Gap ---
            logger.debug(f"Iteration {iterations}: Using TSTT from previous step = {tstt:.5f}")
            if sptt <= self.epsilon:
                logger.warning(f"Iteration {iterations}: SPTT is near zero ({sptt:.5f}). Cannot calculate gap reliably. Stopping assignment.")
                gap = 0
            else:
                gap = abs(tstt / sptt - 1) if tstt > self.epsilon else abs(sptt - tstt) / self.epsilon

            if gap < 0:
                logger.warning(f'Iteration {iterations}: Negative gap encountered ({gap:.5f}), this should not happen. TSTT: {tstt}, SPTT: {sptt}. Stopping assignment.')
                break

            if self.config.verbose:
                self._log_progress(iterations, gap, tstt, sptt, start_time)

            # Check for convergence
            if gap <= self.config.accuracy:
                break

            # --- 3. Check Proportion Tracking Threshold ---
            if self.config.proportions and not threshold_hit:
                if gap <= self.proportion_tracking_threshold:
                    threshold_hit = True
                    logger.info(f"Gap ({gap:.5f}) reached proportion tracking threshold ({self.proportion_tracking_threshold:.5f}) at iteration {iterations}. Will use proportions in next update if available.")
                    if od_link_prop_current is None:
                        logger.warning("Proportion threshold hit, but AON did not track proportions. Re-running AON.")
                        sptt, y_k, sp_edges, eodtt, od_link_prop_current = self._aon_loading(
                            graph, from_cent, to_cent, demands, compute_sptt=True, track_proportions=True
                        )
                    if od_link_prop_accumulated is None and od_link_prop_current is not None:
                        logger.debug("Initializing accumulated proportions.")
                        od_link_prop_accumulated = od_link_prop_current.copy()

            # --- 4. Determine Search Direction (d_k) ---
            # Frank-Wolfe direction is always needed as a base
            fw_direction = {edge: y_k.get(edge, 0.0) - x_k.get(edge, 0.0) for edge in graph.edges()}
            d_k = {}

            if self.config.algorithm == 'MSA':
                alpha = 1.0 / iterations
                logger.debug(f"Iteration {iterations}: Using MSA alpha = {alpha:.6f}")
                d_k = fw_direction

            elif self.config.algorithm == 'FW':
                d_k = fw_direction
                logger.debug(f"Iteration {iterations}: Using FW direction.")

            elif self.config.algorithm == 'CFW':
                logger.debug(f"Iteration {iterations}: Calculating CFW direction.")
                d_k = self._cfw_update(fw_direction, d_k_prev, graph, iterations)

            elif self.config.algorithm == 'BFW':
                logger.debug(f"Iteration {iterations}: Calculating BFW direction.")
                d_k = self._bfw_update(fw_direction, d_k_prev, d_k_prev2, graph, iterations)

            # --- 5. Determine Step Size (alpha) ---
            if self.config.algorithm != 'MSA': # MSA uses fixed alpha
                logger.debug(f"Iteration {iterations}: Finding optimal alpha using line search.")
                alpha = self._find_alpha(d_k, graph) # Line search along direction d_k
                logger.debug(f"Iteration {iterations}: Calculated optimal alpha = {alpha:.6f}")

            # --- 6. Update Flows, Costs, and Proportions ---
            should_update_proportions = self.config.proportions and threshold_hit and (od_link_prop_current is not None) and (od_link_prop_accumulated is not None)

            if should_update_proportions:
                logger.debug(f"Iteration {iterations}: Updating flows, costs, and proportions.")
                x_k, od_link_prop_accumulated = self._update_flows_costs_wprop(
                    graph=graph, alpha=alpha, x_k=x_k, d_k=d_k,
                    od_link_prop_target=od_link_prop_current,
                    od_link_prop_accumulated=od_link_prop_accumulated
                )
            else:
                logger.debug(f"Iteration {iterations}: Updating flows and costs (no proportions).")
                x_k = self._update_flows_costs(
                    graph=graph, alpha=alpha, x_k=x_k, d_k=d_k
                )

            # --- 7. Prepare for Next Iteration ---
            d_k_prev2 = d_k_prev.copy() if d_k_prev is not None else None # Store d_{k-1} as d_{k-2} for next BFW step
            d_k_prev = d_k.copy() # Store current direction d_k as d_{k-1}
            tstt = self._calculate_tstt(graph) # Calculate TSTT for the *next* iteration's gap check

            # End of while loop

        # --- Final Logging and Return ---
        final_iterations = iterations
        if gap > self.config.accuracy and final_iterations >= self.config.max_iter:
            pass # Already logged
        elif gap > self.config.accuracy:
            logger.info(f"Assignment stopped after {final_iterations} iterations. Final gap: {gap:.5f}, TSTT: {tstt:.5f}")

        final_tstt = self._calculate_tstt(graph)
        logger.info(f"Final TSTT calculated: {final_tstt:.5f}")

        if self.config.proportions and od_link_prop_accumulated is not None:
            logger.info("Returning graph, TSTT, SP edges, EODTT, and OD link proportions.")
            return graph, final_tstt, sp_edges, eodtt, od_link_prop_accumulated
        else:
            logger.info("Returning graph, TSTT, SP edges, and EODTT.")
            return graph, final_tstt, sp_edges, eodtt


    def _cfw_update(self,
                    fw_direction: Dict[Tuple, float],
                    d_k_prev: Optional[Dict[Tuple, float]],
                    graph: nx.DiGraph,
                    iteration: int) -> Dict[Tuple, float]:
        """
        Calculates the search direction d_k using standard Conjugate Frank-Wolfe.
        d_k = fw_direction + beta_k * d_{k-1}
        """
        if iteration == 1 or d_k_prev is None:
            logger.debug("CFW Update: Using FW direction (iteration 1 or no d_k_prev).")
            return fw_direction
        else:
            # Calculate beta_k using Hessian approximation
            numerator = 0.0
            denominator = 0.0
            dcost = {edge: data.get('dcost', 0.0) for edge, data in graph.edges.items()}
            active_edges = set(fw_direction.keys()) | set(d_k_prev.keys())

            for edge in active_edges:
                dcost_k = dcost.get(edge, 0.0)
                if dcost_k <= self.epsilon: continue # Skip if derivative is non-positive

                fw_dir_a = fw_direction.get(edge, 0.0)
                d_prev_a = d_k_prev.get(edge, 0.0)

                term_den = d_prev_a * dcost_k * d_prev_a
                denominator += term_den
                numerator += fw_dir_a * dcost_k * d_prev_a

            beta_k = 0.0
            if denominator > self.epsilon:
                beta_k = max(0.0, numerator / denominator) # Clamp beta >= 0 (heuristic)
            else:
                logger.debug(f"CFW beta calculation: Denominator non-positive ({denominator:.2e}), setting beta=0.")

            logger.debug(f"CFW beta calculated: {beta_k:.6f}")

            # Combine directions: d_k = fw_direction + beta_k * d_{k-1}
            d_k = {}
            for edge in active_edges:
                d_k[edge] = fw_direction.get(edge, 0.0) + beta_k * d_k_prev.get(edge, 0.0)
            return d_k


    def _bfw_update(self,
                    fw_direction: Dict[Tuple, float],
                    d_k_prev: Optional[Dict[Tuple, float]],
                    d_k_prev2: Optional[Dict[Tuple, float]],
                    graph: nx.DiGraph,
                    iteration: int) -> Dict[Tuple, float]:
        """
        Calculates the search direction d_k using standard Bi-conjugate Frank-Wolfe.
        d_k = fw_direction + nu_k * d_{k-1} + mu_k * d_{k-2}
        """
        if iteration <= 2 or d_k_prev is None or d_k_prev2 is None:
            logger.debug("BFW Update: Using CFW direction (iteration <= 2 or missing prev directions).")
            return self._cfw_update(fw_direction, d_k_prev, graph, iteration)
        else:
            # Calculate nu_k and mu_k using Hessian approximations
            nu_num = 0.0
            nu_den = 0.0
            mu_num = 0.0
            mu_den = 0.0
            dcost = {edge: data.get('dcost', 0.0) for edge, data in graph.edges.items()}
            active_edges = set(fw_direction.keys()) | set(d_k_prev.keys()) | set(d_k_prev2.keys())

            for edge in active_edges:
                dcost_k = dcost.get(edge, 0.0)
                if dcost_k <= self.epsilon:
                    continue # Skip if derivative non-positive

                fw_dir_a = fw_direction.get(edge, 0.0)
                d_prev_a = d_k_prev.get(edge, 0.0)
                d_prev2_a = d_k_prev2.get(edge, 0.0)

                # Denominators (Hessian approx terms)
                term_nu_den = d_prev_a * dcost_k * d_prev_a
                nu_den += term_nu_den
                term_mu_den = d_prev2_a * dcost_k * d_prev2_a
                mu_den += term_mu_den

                # Numerators
                nu_num += fw_dir_a * dcost_k * d_prev_a
                mu_num += fw_dir_a * dcost_k * d_prev2_a

            nu_k = 0.0
            if nu_den > self.epsilon:
                nu_k = max(0.0, nu_num / nu_den) # Clamp nu >= 0
            else:
                logger.debug(f"BFW nu calculation: Denominator non-positive ({nu_den:.2e}), setting nu=0.")

            mu_k = 0.0
            if mu_den > self.epsilon:
                mu_k = max(0.0, mu_num / mu_den) # Clamp mu >= 0
            else:
                logger.debug(f"BFW mu calculation: Denominator non-positive ({mu_den:.2e}), setting mu=0.")

            logger.debug(f"BFW coefficients calculated: nu={nu_k:.6f}, mu={mu_k:.6f}")

            # Combine directions: d_k = fw_direction + nu_k * d_{k-1} + mu_k * d_{k-2}
            d_k = {}
            for edge in active_edges:
                d_k[edge] = (fw_direction.get(edge, 0.0) +
                             nu_k * d_k_prev.get(edge, 0.0) +
                             mu_k * d_k_prev2.get(edge, 0.0))
            return d_k

    def _assign_tapas(self, graph: nx.DiGraph, from_cent: Dict, to_cent: Dict, demands: Dict, start_time: float) -> Tuple[nx.DiGraph, float, Dict, Dict]:
        """Executes the TAPAS assignment algorithm (requires external 'tapas' module)."""

        logger.debug("Performing initial AON loading for TAPAS...")
        _, x_bar, _, _ = self._aon_loading(
            graph, from_cent, to_cent, demands,
            compute_sptt=False, track_proportions=False
        )
        for u, v, data in graph.edges(data=True):
            flow = x_bar.get((u, v), 0.0)
            data['flow'] = flow
            try:
                data['cost'], data['dcost'] = self.config.cost_function(data, flow)
            except Exception as e:
                logger.error(f"Error in cost function during TAPAS init for edge {(u,v)}: {e}", exc_info=True)
                raise ValueError(f"Cost function failed during TAPAS init for edge {(u,v)}")

        logger.debug("Calling TAPAS core function...")
        # Call the updated TAPAS wrapper with proportions support
        results = TAPAS(
            graph=graph,
            from_cent=from_cent,
            to_cent=to_cent,
            demand=demands,
            cost_function=self.config.cost_function,
            max_iter=self.config.max_iter,
            accuracy=self.config.accuracy,
            start_time=start_time,
            verbose=self.config.verbose,
            proportions=self.config.proportions
        )
        logger.info("TAPAS assignment complete.")

        # Unpack results based on whether proportions were requested
        if self.config.proportions:
            if not isinstance(results, tuple) or len(results) != 5:
                logger.error("TAPAS wrapper with proportions did not return the expected 5 values.")
                raise ValueError("TAPAS assignment with proportions failed to return correct results.")
            final_graph, final_tstt, final_sp_edges, final_eodtt, od_link_prop = results
        else:
            if not isinstance(results, tuple) or len(results) != 6:
                logger.error("TAPAS wrapper did not return the expected 6 values.")
                raise ValueError("TAPAS assignment failed to return correct results.")
            final_graph, final_tstt, final_sp_edges, final_eodtt, final_gap, final_iterations = results

        # Ensure costs are updated in the final graph (redundant if TAPAS does it, but safe)
        for u, v, data in final_graph.edges(data=True):
            try:
                flow = max(0.0, data.get('flow', 0.0))
                data['cost'], data['dcost'] = self.config.cost_function(data, flow)
            except Exception as e:
                logger.warning(f"Could not update final cost for edge ({u},{v}) after TAPAS: {e}")
                data['cost'], data['dcost'] = float('inf'), 0.0

        # Return results with proportions if requested
        if self.config.proportions:
            return final_graph, final_tstt, final_sp_edges, final_eodtt, od_link_prop
        else:
            return final_graph, final_tstt, final_sp_edges, final_eodtt

    def _aon_loading(self, graph: nx.DiGraph, from_cent: Dict, to_cent: Dict, demands: Dict, compute_sptt: bool, track_proportions: bool = False) -> Union[Tuple[float, Dict, Dict, Dict], Tuple[float, Dict, Dict, Dict, Dict]]:
        """Performs All-or-Nothing (AON) loading based on current edge costs."""
        y_k = defaultdict(float)
        od_link_prop = None
        if track_proportions:
            od_link_prop = {}
        sp_edges = {} if compute_sptt else None
        eodtt = {} if compute_sptt else None
        sptt = 0.0
        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            logger.warning("AON Loading: Graph has no nodes or edges.")
            if track_proportions:
                return 0.0, {}, {}, {}, {}
            return 0.0, {}, {}, {}
        for ozone, onode in from_cent.items():
            if onode not in graph:
                logger.warning(f"AON Loading: Origin node {onode} for zone {ozone} not in graph. Skipping origin.")
                continue
            try:
                ttd, pathd = nx.single_source_dijkstra(graph, onode, weight='cost')
            except nx.NodeNotFound:
                continue
            except nx.NetworkXNoPath:
                continue
            except Exception as e:
                logger.error(f"AON Loading: Dijkstra failed for origin node {onode} (Zone {ozone}): {e}", exc_info=True)
                continue
            for dzone, dnode in to_cent.items():
                if ozone == dzone:
                    continue
                if dnode not in graph:
                    logger.warning(f"AON Loading: Destination node {dnode} for zone {dzone} not in graph. Skipping OD pair ({ozone}, {dzone}).")
                    continue
                dem = demands.get((ozone, dzone), 0.0)
                if dem <= 0:
                    continue
                if dnode not in ttd:
                    continue
                tt = ttd[dnode]
                path_nodes = pathd[dnode]
                if compute_sptt:
                    sptt += tt * dem
                    #  edge_path = list(zip(path_nodes[:-1], path_nodes[1:]))
                    sp_edges[(ozone, dzone)] = path_nodes
                    eodtt[(ozone, dzone)] = tt
                current_edge_path = list(zip(path_nodes[:-1], path_nodes[1:]))
                for edge in current_edge_path:
                    y_k[edge] += dem
                    if track_proportions:
                        if edge not in od_link_prop:
                            od_link_prop[edge] = {}
                        if (ozone, dzone) not in od_link_prop[edge]:
                            od_link_prop[edge][(ozone, dzone)] = 0.0
                        od_link_prop[edge][(ozone, dzone)] += float(dem)  # Ensure Python float
        if track_proportions:
            return sptt, dict(y_k), sp_edges, eodtt, od_link_prop
        return sptt, dict(y_k), sp_edges, eodtt

    def _find_alpha(self, d_k: Dict[Tuple, float], graph: nx.DiGraph) -> float:
        """Finds the optimal step size (alpha) along search direction d_k using bisection."""
        lbound, ubound = 0.0, 1.0
        current_flow = {edge: data['flow'] for edge, data in graph.edges.items()}
        active_edges = set(d_k.keys()) | set(current_flow.keys())
        while ubound - lbound > self.epsilon:
            alpha = (lbound + ubound) / 2.0
            zprime = 0.0
            for edge in active_edges:
                data = graph.edges.get(edge)
                if data is None:
                    continue
                flow_k = current_flow.get(edge, 0.0)
                direction = d_k.get(edge, 0.0)
                if abs(direction) < self.epsilon:
                    continue
                tmp_flow = flow_k + alpha * direction
                try:
                    tmp_cost, _ = self.config.cost_function(data, tmp_flow)
                except Exception as e:
                    logger.error(f"Error in cost function during line search for edge {edge} with flow {tmp_flow}: {e}", exc_info=True)
                    return alpha
                zprime += direction * tmp_cost
            if abs(zprime) < self.epsilon:
                return alpha
            elif zprime > 0:
                ubound = alpha
            else:
                lbound = alpha
        return max(0.0, min(1.0, (lbound + ubound) / 2.0))

    def _update_flows_costs(self, graph: nx.DiGraph, alpha: float, x_k: Dict[Tuple, float], d_k: Dict[Tuple, float]) -> Dict[Tuple, float]:
        """Updates graph edge flows and costs: x_{k+1} = x_k + alpha * d_k"""
        new_flows = {}
        active_edges = set(x_k.keys()) | set(d_k.keys())
        for edge in active_edges:
            data = graph.edges.get(edge)
            if data is None:
                continue
            flow_k = x_k.get(edge, 0.0)
            direction = d_k.get(edge, 0.0)
            new_flow = flow_k + alpha * direction
            new_flows[edge] = new_flow
            data['flow'] = new_flow
            try:
                data['cost'], data['dcost'] = self.config.cost_function(data, new_flow)
            except Exception as e:
                logger.error(f"Error in cost function during update for edge {edge} with flow {new_flow}: {e}", exc_info=True)
                data['cost'] = float('inf')
                data['dcost'] = 0.0
        return new_flows

    def _update_flows_costs_wprop(self, graph: nx.DiGraph, alpha: float, x_k: Dict[Tuple, float], d_k: Dict[Tuple, float], od_link_prop_target: Dict[Tuple, Dict[Tuple, float]], od_link_prop_accumulated: Dict[Tuple, Dict[Tuple, float]]) -> Tuple[Dict[Tuple, float], Dict[Tuple, Dict[Tuple, float]]]:
        """Updates flows, costs, and proportions: P_{k+1} = (1 - alpha) * P_k + alpha * P_target"""
        new_flows = {}
        new_od_link_prop_accumulated = {}
        active_edges = set(x_k.keys()) | set(d_k.keys()) | set(od_link_prop_accumulated.keys()) | set(od_link_prop_target.keys())
        for edge in active_edges:
            data = graph.edges.get(edge)
            if data is None:
                continue
            flow_k = x_k.get(edge, 0.0)
            direction = d_k.get(edge, 0.0)
            new_flow = flow_k + alpha * direction
            new_flows[edge] = new_flow
            data['flow'] = new_flow
            prop_accum_edge = od_link_prop_accumulated.get(edge, {})
            prop_target_edge = od_link_prop_target.get(edge, {})
            edge_od_pairs = set(prop_accum_edge.keys()) | set(prop_target_edge.keys())
            for od_pair in edge_od_pairs:
                prop_accum_val = prop_accum_edge.get(od_pair, 0.0)
                prop_target_val = prop_target_edge.get(od_pair, 0.0)
                new_prop_val = (1.0 - alpha) * prop_accum_val + alpha * prop_target_val
                if new_prop_val > self.epsilon:
                    if edge not in new_od_link_prop_accumulated:
                        new_od_link_prop_accumulated[edge] = {}
                    new_od_link_prop_accumulated[edge][od_pair] = float(new_prop_val)  # Ensure Python float
            try:
                data['cost'], data['dcost'] = self.config.cost_function(data, new_flow)
            except Exception as e:
                logger.error(f"Error in cost function during proportion update for edge {edge} with flow {new_flow}: {e}", exc_info=True)
                data['cost'] = float('inf')
                data['dcost'] = 0.0
        return new_flows, new_od_link_prop_accumulated

    def _calculate_tstt(self, graph: nx.DiGraph) -> float:
        """Calculates the Total System Travel Time (TSTT)."""
        logger.debug("Calculating TSTT...")
        tstt = 0.0
        for u, v, data in graph.edges(data=True):
            flow = data.get('flow', 0.0)
            cost = data.get('cost', 0.0)
            if flow > self.epsilon and cost is not None and cost != float('inf'):
                tstt += cost * flow
        logger.debug(f"Calculated TSTT: {tstt}")
        return tstt

    def _log_progress(self, iterations: int, gap: float, tstt: float, sptt: float, start_time: float, max_iter_reached: bool = False) -> None:
        """Logs the progress of the assignment algorithm."""
        elapsed_time = time.perf_counter() - start_time
        if max_iter_reached:
            logger.warning(f'Maximum iterations ({self.config.max_iter}) reached! Assignment may not be converged.')
            logger.warning(f"Assignment stopped after {elapsed_time:.3f} seconds. Iterations: {iterations}, Final gap: {gap:.5f}, TSTT: {tstt:.5f}")
            return
        if gap <= self.config.accuracy:
            logger.info(f"Assignment converged in {iterations} iterations.")
            logger.info(f"Assignment took {elapsed_time:.3f} seconds.")
            logger.info(f"Final Gap: {gap:.5f}, Total System Travel Time: {tstt:.5f}")
            return
        logger.info(f"Iteration: {iterations}; Current gap: {gap:.5f}; TSTT: {tstt:.5f}; SPTT: {sptt:.5f} ; Elapsed: {elapsed_time:.2f}s")


class TripGenerator:
    """Handles trip generation and saving."""

    @staticmethod
    def save_trips(network_graph: nx.DiGraph,
                from_cent: Dict,
                to_cent: Dict,
                demands: Dict,
                save_path: Path,
                edges_file: str) -> None:
        """Save trip information for visualization."""
        logger.info(f"Starting trip generation for saving to {save_path}...")
        logger.debug("Performing temporary assignment to get shortest paths...")
        assignconfig = AssignmentConfig(
            algorithm='BFW',
            cost_function=bpr_function,
            accuracy=0.0001,
            max_iter=1000,
            verbose=False
        )
        assignment = TrafficAssignment(assignconfig)
        try:
            G, _, sp_edges, _, _ = assignment.assign_traffic(
                network_graph,
                from_cent,
                to_cent,
                demands
            )
            logger.debug(f"Assignment complete. Found {len(sp_edges)} shortest paths.")
        except Exception as e:
            logger.exception(f"Error during temporary assignment for trip generation.-> {e}") 
            raise

        # Create reverse mappings
        logger.debug("Creating reverse centroid mappings...")
        rev_from = {v: k for k, v in from_cent.items()}
        rev_to = {v: k for k, v in to_cent.items()}

        # Load edges data
        try:
            logger.debug(f"Loading edges geometry from {edges_file}...")
            edges = gpd.read_file(edges_file)
            logger.debug(f"Loaded {len(edges)} edges.")
        except Exception as e:
            logger.exception(f"Error loading edges file {edges_file}.-> {e}")
            raise

        # Generate trips
        logger.info("Generating trip path and timestamp data...")
        trips = TripGenerator._generate_trips(
            G, sp_edges, edges, demands,
            rev_from, rev_to
        )
        logger.info(f"Generated data for {len(trips)} trips.")

        # Save to protobuf
        logger.info("Saving trips to protobuf format...")
        TripGenerator._save_to_protobuf(trips, save_path)
        logger.info(f"Trips saved successfully to {save_path / 'trips.pb'}")

    @staticmethod
    def _generate_trips(graph: nx.DiGraph,
                    sp_edges: Dict,
                    edges: gpd.GeoDataFrame,
                    demands: Dict,
                    rev_from: Dict,
                    rev_to: Dict) -> List[Dict]:
        """Generate trip information."""
        logger.debug("Starting _generate_trips...")
        trips = []
        if not demands:
            logger.warning("Demands dictionary is empty. No trips will be generated.")
            return trips
        min_demand, max_demand = min(demands.values()), max(demands.values())
        demand_range = max(1e-9, max_demand - min_demand)
        logger.debug(f"Demand range for scaling: min={min_demand}, max={max_demand}")

        processed_paths = 0
        for od_pair, nodes in sp_edges.items():
            try:
                node_list = nodes[1:-1]
                if not node_list:
                    logger.warning(f"Path for OD {od_pair} has no intermediate nodes. Skipping.")
                    continue
                edge_list = [(node_list[i], node_list[i+1])
                            for i in range(len(node_list)-1)]

                trip_path = []
                timestamps = [0.0]

                valid_path = True
                for edge in edge_list:
                    try:
                        edge_geom_series = edges[
                            (edges['from'] == edge[0]) &
                            (edges['to'] == edge[1])
                        ]['geometry']

                        if edge_geom_series.empty:
                            logger.warning(f"Geometry not found for edge {edge} in path for OD {od_pair}. Skipping path.")
                            valid_path = False
                            break
                        geom = edge_geom_series.iloc[0]

                        simplified = geom.simplify(0.0001)
                        coords = list(simplified.coords)

                        # Get edge cost (travel time) and length from graph
                        edge_data = graph.edges[edge]
                        edge_cost = edge_data.get('cost', 0.0)
                        edge_length = edge_data.get('length', 0.0)

                        # Calculate speed (handle division by zero)
                        speed = (edge_length / edge_cost) if edge_cost > 1e-9 else 0.0

                        # Add coordinates and timestamps
                        if not trip_path:
                            trip_path.append(coords[0])

                        for i in range(len(coords) - 1):
                            segment_start = coords[i]
                            segment_end = coords[i+1]
                            trip_path.append(segment_end)

                            # Calculate time for this segment based on haversine distance and edge speed
                            segment_dist = AssignmentCalculator.haversine(
                                segment_start[0], segment_start[1],
                                segment_end[0], segment_end[1]
                            )
                            segment_time = (segment_dist / speed) if speed > 1e-9 else 0.0
                            timestamps.append(timestamps[-1] + segment_time)

                    except KeyError:
                        logger.warning(f"Edge {edge} not found in graph data for OD {od_pair}. Skipping path.")
                        valid_path = False
                        break
                    except Exception as e:
                        logger.error(f"Error processing edge {edge} for OD {od_pair}: {e}", exc_info=True)
                        valid_path = False
                        break

                if not valid_path:
                    continue

                start_time = timestamps[0]
                duration = timestamps[-1] - start_time

                current_demand = demands.get(od_pair, min_demand)
                demand_scale = 16.0 + 48.0 * (current_demand - min_demand) / demand_range

                trips.append({
                    'path': trip_path,
                    'timestamps': timestamps,
                    'demands': demand_scale,
                    'startTime': start_time,
                    'duration': duration
                })
                processed_paths += 1

            except Exception as e:
                logger.error(f"Error generating trip data for OD pair {od_pair}: {e}", exc_info=True)

        logger.debug(f"Finished _generate_trips. Generated data for {processed_paths} paths.")
        return trips

    @staticmethod
    def _save_to_protobuf(trips: List[Dict], save_path: Path) -> None:
        """Save trips to protobuf format."""
        logger.debug("Starting _save_to_protobuf...")
        if not trips:
            logger.warning("No trips to save to protobuf.")
            return

        try:
            # Ensure save directory exists
            save_path.mkdir(parents=True, exist_ok=True)
            protobuf_file = save_path / 'trips.pb'

            max_time = max(trip['timestamps'][-1] for trip in trips if trip['timestamps']) # Handle empty timestamps
            logger.debug(f"Max timestamp across all trips: {max_time}")

            paths_proto = Paths()
            for trip in trips:
                path = paths_proto.paths.add()

                # Add coordinates
                for coord in trip['path']:
                    location = path.path.add()
                    location.longitude = coord[0]
                    location.latitude = coord[1]

                # Add timestamps and metadata
                path.timestamps.extend(trip['timestamps'])
                path.demands = trip['demands']
                path.startTime = trip['startTime']
                path.duration = trip['duration']
                path.maxtime = max_time # Add max_time to each path message

            # Save to file
            logger.debug(f"Serializing and writing protobuf data to {protobuf_file}...")
            with open(protobuf_file, 'wb') as f:
                f.write(paths_proto.SerializeToString())
            logger.debug("Finished _save_to_protobuf.")

        except Exception as e:
            logger.exception(f"Error saving trips to protobuf file {protobuf_file}: {e}")
            raise

class DataFormatConverter:
    """Handles data format conversions."""

    @staticmethod
    def list2str(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Convert list columns to string type."""
        logger.debug("Converting list-like object columns to string...")
        cols_converted = 0
        for col in gdf.columns:
            if gdf[col].dtype == 'object' and gdf[col].apply(lambda x: isinstance(x, (list, tuple))).any():
                logger.debug(f"Converting column '{col}' to string.")
                gdf[col] = gdf[col].astype(str)
                cols_converted += 1
        logger.debug(f"Converted {cols_converted} columns to string.")
        return gdf

    @staticmethod
    def convert(form: str, path: Path, zone_folder: Path) -> None:
        """Convert between data formats."""
        logger.info(f"Starting data conversion: {form} for path {path}")
        if form == 'rex2rpy':
            DataFormatConverter._rex_to_rpy(path)
        elif form == 'rpy2rex':
            DataFormatConverter._rpy_to_rex(path, zone_folder)
        else:
            logger.error(f"Unknown conversion format requested: {form}")
            raise ValueError(f"Unknown conversion format: {form}")
        logger.info(f"Data conversion {form} completed.")

    @staticmethod
    def _rex_to_rpy(path: Path) -> None:
        """Convert from RapidEx to RapidXPy format."""
        logger.info(f"Converting RapidEx format to RapidXPy format in {path}")
        # Validate input files
        required_files = [
            path / 'edges.geojson',
            path / 'nodes.geojson',
            path / 'net.csv',
            path / 'aggrid_polygon.geojson'
            # centroid can be csv or gpkg, traveltimes is a folder
        ]
        logger.debug("Checking for required RapidEx input files...")
        for file in required_files:
            if not file.exists():
                logger.error(f"Required RapidEx file missing for conversion: {file}")
                raise FileNotFoundError(f"Required file missing: {file}")

        travel_times_folder = path / 'traveltimes'
        if not travel_times_folder.is_dir():
            logger.warning(f"Travel times folder not found: {travel_times_folder}. Skipping travel time conversion.")

        # Load data
        try:
            logger.debug("Loading RapidEx data files...")
            edges = gpd.read_file(path/'edges.geojson')
            nodes = gpd.read_file(path/'nodes.geojson')
            network = pd.read_csv(path/'net.csv')
            zones = gpd.read_file(path/'aggrid_polygon.geojson')

            centroid_gpkg = path/'centroid.gpkg'
            centroid_csv = path/'centroid.csv'
            if centroid_gpkg.exists():
                centroids = gpd.read_file(centroid_gpkg)
                logger.debug("Loaded centroids from centroid.gpkg")
            elif centroid_csv.exists():
                centroids = pd.read_csv(centroid_csv)
                logger.debug("Loaded centroids from centroid.csv")
            else:
                logger.error("Centroid file (centroid.gpkg or centroid.csv) not found.")
                raise FileNotFoundError("Centroid file not found.")
            logger.debug("Finished loading RapidEx data.")

        except Exception as e:
            logger.exception(f"Error loading RapidEx data files: {e}")
            raise

        # Create directories
        output_dirs = [
            'rapidxpy',
            'rapidxpy/Network',
            'rapidxpy/Traveltimes',
            'rapidxpy/Zones_default'
        ]
        logger.debug("Creating RapidXPy output directories...")
        for dir_name in output_dirs:
            try:
                (path / dir_name).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.exception(f"Failed to create directory {path / dir_name}: {e}")
                raise
        logger.debug("RapidXPy output directories created.")

        # Process and save data
        logger.info("Processing and saving data to RapidXPy format...")
        try:
            DataFormatConverter._process_rex_data(
                path, edges, nodes, network,
                travel_times_folder, zones, centroids
            )
            logger.info("Finished processing and saving data to RapidXPy format.")
        except Exception as e:
            logger.exception(f"Error during RapidEx to RapidXPy data processing: {e}")
            raise

    @staticmethod
    def _rpy_to_rex(path: Path, zone_folder: Optional[Path]) -> None:
        """Convert from RapidXPy to RapidEx format."""
        logger.info(f"Converting RapidXPy format to RapidEx format in {path}")
        if zone_folder is None:
            logger.debug("No zone folder specified, searching for 'Zones_*' folders...")
            zone_folders = sorted([
                f for f in path.iterdir()
                if f.is_dir() and 'Zones_' in f.name
            ])

            if not zone_folders:
                logger.error("No 'Zones_*' folders found in the specified path.")
                raise FileNotFoundError("No zone folders found")
            if len(zone_folders) > 1:
                logger.warning(f"Multiple zone folders found: {[f.name for f in zone_folders]}. Using first one: {zone_folders[0].name}")
            zone_folder = zone_folders[0]
            logger.debug(f"Using zone folder: {zone_folder}")
        else:
            logger.debug(f"Using specified zone folder: {zone_folder}")


        zones_file = path / zone_folder / 'Zones.geojson'
        logger.debug(f"Checking for Zones file: {zones_file}")
        try:
            zones = gpd.read_file(zones_file)
            logger.debug("Loaded Zones.geojson")
        except Exception as e:
            logger.error(f"Failed to read Zones file: {zones_file}. Error: {e}", exc_info=True)
            raise FileNotFoundError(f"No Zones.geojson found or failed to read: {zones_file}")

        # Create directories
        output_dirs = [
            'rapidex',
            'rapidex/.network',
            'rapidex/traveltimes',
            'rapidex/models',
            'rapidex/traveldemands'
        ]
        logger.debug("Creating RapidEx output directories...")
        for dir_name in output_dirs:
            try:
                (path / dir_name).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.exception(f"Failed to create directory {path / dir_name}: {e}")
                raise
        logger.debug("RapidEx output directories created.")

        # Process and save data
        logger.info("Processing and saving data to RapidEx format...")
        try:
            DataFormatConverter._process_rpy_data(
                path, zone_folder, zones
            )
            logger.info("Finished processing and saving data to RapidEx format.")
        except Exception as e:
            logger.exception(f"Error during RapidXPy to RapidEx data processing: {e}")
            raise

    @staticmethod
    def _process_rex_data(path: Path,
                        edges: gpd.GeoDataFrame,
                        nodes: gpd.GeoDataFrame,
                        network: pd.DataFrame,
                        travel_times_folder: Path,
                        zones: gpd.GeoDataFrame,
                        centroids: Union[gpd.GeoDataFrame, pd.DataFrame]) -> None:
        """Process RapidEx data for conversion."""
        logger.debug("Processing RapidEx network data for RapidXPy...")
        DataFormatConverter._process_network_to_rpy(path, edges, nodes, network)
        logger.debug("Processing RapidEx travel times for RapidXPy...")
        DataFormatConverter._process_traveltimes_to_rpy(path, travel_times_folder)
        logger.debug("Processing RapidEx zones/centroids for RapidXPy...")
        DataFormatConverter._process_zones_to_rpy(path, zones, nodes, centroids)
        logger.debug("Processing RapidEx custom solution for RapidXPy...")
        DataFormatConverter._process_custom_solution_to_rpy(path)
        logger.debug("Processing RapidEx models for RapidXPy...")
        DataFormatConverter._process_models_to_rpy(path)

    @staticmethod
    def _process_network_to_rpy(path: Path, edges: gpd.GeoDataFrame,
                            nodes: gpd.GeoDataFrame, network: pd.DataFrame) -> None:
        """Process network data for RapidXPy format."""
        try:
            logger.debug("Processing edges for RapidXPy...")
            edges.rename(columns={'edgeID': 'EdgeID'}, inplace=True)
            DataFormatConverter.list2str(edges).to_file(
                path/'rapidxpy/Network/edges.geojson', driver='GeoJSON')
            logger.debug("Saved rapidxpy/Network/edges.geojson")

            logger.debug("Processing nodes for RapidXPy...")
            nodes.rename(columns={'nodeID': 'NodeID'}, inplace=True)
            DataFormatConverter.list2str(nodes).to_file(
                path/'rapidxpy/Network/nodes.geojson', driver='GeoJSON')
            logger.debug("Saved rapidxpy/Network/nodes.geojson")

            logger.debug("Processing network CSV for RapidXPy...")
            network.rename(columns={
                'length (m)': 'length',
                'capacity (veh/hr)': 'capacity',
                'Free Flow Time (min)': 'fft',
                'B': 'alpha',
                'Power': 'beta',
                'Type': 'etype'
            }, inplace=True)
            network['etype'] = network['etype'].apply(
                lambda x: 'Arterial' if x == 'A' else ('Freeway' if x == 'F' else x)) # Handle other types?
            network.to_csv(path/'rapidxpy/Network/Network.csv', index=False)
            logger.debug("Saved rapidxpy/Network/Network.csv")
        except Exception as e:
            logger.exception(f"Error processing network data for RapidXPy.-> {e}")
            raise

    @staticmethod
    def _process_traveltimes_to_rpy(path: Path, travel_times_folder: Path) -> None:
        """Process travel times for RapidXPy format."""
        if not travel_times_folder.exists():
            logger.warning(f"Travel times folder {travel_times_folder} does not exist. Skipping.")
            return
        try:
            processed_count = 0
            for file_folder in travel_times_folder.iterdir():
                if file_folder.is_dir():
                    link_tt_file = file_folder / 'link_TT.csv'
                    if link_tt_file.is_file():
                        logger.debug(f"Processing travel time file: {link_tt_file}")
                        tt = pd.read_csv(link_tt_file)
                        tt.rename(columns={'From': 'from', 'To': 'to'}, inplace=True)
                        new_filename = f"{file_folder.name}.csv"
                        output_path = path / 'rapidxpy/Traveltimes' / new_filename
                        tt.to_csv(output_path, index=False)
                        logger.debug(f"Saved travel times to {output_path}")
                        processed_count += 1
                    else:
                        logger.warning(f"link_TT.csv not found in {file_folder}. Skipping.")
            logger.info(f"Processed {processed_count} travel time files.")
        except Exception as e:
            logger.exception(f"Error processing travel times for RapidXPy.-> {e}")
            raise

    @staticmethod
    def _process_zones_to_rpy(path: Path, zones: gpd.GeoDataFrame,
                        nodes: gpd.GeoDataFrame,
                        centroids: Union[gpd.GeoDataFrame, pd.DataFrame]) -> None:
        """Process zones and centroids for RapidXPy format."""
        try:
            logger.debug("Processing zones for RapidXPy...")
            zones.rename(columns={'fishnetID': 'ZoneID'}, inplace=True)
            DataFormatConverter.list2str(zones).to_file(
                path/'rapidxpy/Zones_default/Zones.geojson', driver='GeoJSON'
            )
            logger.debug("Saved rapidxpy/Zones_default/Zones.geojson")

            logger.debug("Processing centroids for RapidXPy...")
            if isinstance(centroids, pd.DataFrame):
                logger.debug("Centroids are in DataFrame format. Adding geometry from nodes...")
                # Ensure 'osmid' column exists in centroids and 'NodeID' in nodes
                if 'osmid' not in centroids.columns:
                    logger.error("'osmid' column missing in centroid.csv")
                    raise ValueError("'osmid' column missing in centroid.csv")
                if 'NodeID' not in nodes.columns:
                    logger.error("'NodeID' column missing in nodes.geojson")
                    raise ValueError("'NodeID' column missing in nodes.geojson")

                # Create a mapping from NodeID to geometry for faster lookup
                node_geom_map = nodes.set_index('NodeID')['geometry']
                centroids['geometry'] = centroids['osmid'].map(node_geom_map)
                # Check for missing geometries
                missing_geom_count = centroids['geometry'].isnull().sum()
                if missing_geom_count > 0:
                    logger.warning(f"{missing_geom_count} centroids could not be mapped to node geometries.")
                centroids = gpd.GeoDataFrame(centroids.dropna(subset=['geometry']), geometry='geometry', crs=nodes.crs) # Use nodes CRS
                logger.debug("Converted centroids DataFrame to GeoDataFrame.")

            # Rename columns if they exist
            centroids.rename(columns={'fishnetID': 'ZoneID', 'osmid': 'NodeID'}, inplace=True, errors='ignore')

            DataFormatConverter.list2str(centroids).to_file(
                path/'rapidxpy/Zones_default/Centroids.geojson', driver='GeoJSON')
            logger.debug("Saved rapidxpy/Zones_default/Centroids.geojson")
        except Exception as e:
            logger.exception(f"Error processing zones and centroids for RapidXPy.-> {e}")
            raise

    @staticmethod
    def _process_custom_solution_to_rpy(path: Path) -> None:
        """Process custom solution for RapidXPy format."""
        custom_solution_file = path / 'customInitialSolution.csv'
        if not custom_solution_file.exists():
            logger.info("customInitialSolution.csv not found. Skipping.")
            return
        try:
            logger.debug(f"Processing {custom_solution_file}...")
            custom_initial_solution = pd.read_csv(custom_solution_file)
            # Check expected columns before renaming
            if list(custom_initial_solution.columns) == ['Centroid', 'Proportion', 'Proportion']:
                custom_initial_solution.columns = ['ZoneID', 'Productions', 'Attractions']
                output_path = path / 'rapidxpy/Zones_default/CustomInitialSolution.csv'
                custom_initial_solution.to_csv(output_path, index=False)
                logger.debug(f"Saved custom solution to {output_path}")
            else:
                logger.warning(f"Unexpected columns in {custom_solution_file}: {list(custom_initial_solution.columns)}. Expected ['Centroid', 'Proportion', 'Proportion']. Skipping rename and save.")

        except Exception as e:
            logger.exception(f"Error processing custom solution file {custom_solution_file}.-> {e}")
            # Decide whether to raise or just log

    @staticmethod
    def _process_models_to_rpy(path: Path) -> None:
        """Process models data for RapidXPy format."""
        models_path = path / 'models'
        if not models_path.exists():
            logger.info(f"Models directory {models_path} not found. Skipping model processing.")
            return

        logger.debug(f"Processing models in {models_path}...")
        processed_models = 0
        for model in models_path.iterdir():
            if model.is_dir():
                logger.debug(f"Processing model directory: {model.name}")
                model_dest = path / 'rapidxpy/Zones_default/Results' / model.name

                # Process assignments
                ass_path = model / 'assignments'
                if ass_path.exists() and ass_path.is_dir():
                    logger.debug("Processing assignments subfolder...")
                    ass_dest = model_dest / 'Assignments'
                    ass_dest.mkdir(parents=True, exist_ok=True)
                    DataFormatConverter._process_model_files(ass_path, ass_dest, 'assignments')
                else:
                    logger.debug(f"Assignments subfolder not found or not a directory in {model.name}. Skipping.")

                # Process estimates
                est_path = model / 'estimates'
                if est_path.exists() and est_path.is_dir():
                    logger.debug("Processing estimates subfolder...")
                    est_dest = model_dest / 'Estimation'
                    est_dest.mkdir(parents=True, exist_ok=True)
                    DataFormatConverter._process_model_files(est_path, est_dest, 'estimation')
                else:
                    logger.debug(f"Estimates subfolder not found or not a directory in {model.name}. Skipping.")
                processed_models += 1
        logger.info(f"Processed {processed_models} model directories.")

    @staticmethod
    def _process_model_files(source_path: Path, dest_path: Path, folder_type: str) -> None:
        """Process individual model files."""
        logger.debug(f"Processing files in {source_path} for type '{folder_type}' -> {dest_path}")

        if folder_type == 'assignments':
            df_processors = {
                'linkTT': (
                    ['from', 'to', 'volume', 'TT'],
                    {'from': 'From', 'to': 'To',
                        'volume': 'Volume', 'TT': 'EstimatedTT'},
                    lambda f: f.name
                ),
                'ODTT': (
                    None,
                    {'origin': 'Origin', 'destination': 'Destination'},
                    lambda f: f.name
                ),
                'zone_emissions': (
                    None,
                    {'fishnetid': 'ZoneID'},
                    lambda f: 'zone_emissions.csv'
                )
            }
        else:  # estimation
            df_processors = {
                'LinkResults': (
                    ['from', 'to', 'Volume', 'TT'],
                    {'from': 'From', 'to': 'To', 'TT': 'EstimatedTT'},
                    lambda f: f'Elite{int(f.stem.split("Elite")[1].split("Link")[0])-1}LinkResults.csv'
                ),
                'ODResults': (
                    None,
                    {'TT': 'EstimatedTT', 'TTobs': 'ObservedTT'},
                    lambda f: f'Elite{int(f.stem.split("Elite")[1].split("OD")[0])-1}ODResults.csv'
                ),
                'Validation': (
                    None,
                    {},
                    lambda f: f'Elite{int(f.stem.split("Elite")[1].split("Validation")[0])-1}Stats.csv'
                )
            }

        processed_files = 0
        copied_files = 0
        for file in source_path.iterdir():
            if file.is_file() and file.suffix.lower() == '.csv':
                logger.debug(f"Processing file: {file.name}")
                try:
                    df = pd.read_csv(file)
                    processed = False
                    for key, (columns, rename_dict, new_name_func) in df_processors.items():
                        if key in file.name:
                            logger.debug(f"Matched file '{file.name}' with processor key '{key}'")
                            if columns:
                                missing_cols = [col for col in columns if col not in df.columns]
                                if missing_cols:
                                    logger.warning(f"Missing required columns {missing_cols} in {file.name} for processor '{key}'. Skipping processing.")
                                    continue
                                df = df[columns].copy()
                            df.rename(columns=rename_dict, inplace=True)
                            new_filename = new_name_func(file)
                            output_filepath = dest_path / new_filename
                            df.to_csv(output_filepath, index=False)
                            logger.debug(f"Saved processed data to {output_filepath}")
                            processed = True
                            processed_files += 1
                            break

                    if not processed:
                        logger.debug(f"No specific processor matched for {file.name}. Copying file.")
                        output_filepath = dest_path / file.name
                        file.rename(output_filepath)
                        logger.debug(f"Copied file to {output_filepath}")
                        copied_files += 1

                except Exception as e:
                    logger.error(f"Error processing file {file.name}: {e}", exc_info=True)
            elif file.is_file():
                logger.debug(f"Skipping non-CSV file: {file.name}")

        logger.debug(f"Finished processing files in {source_path}. Processed: {processed_files}, Copied: {copied_files}")


    @staticmethod
    def _process_rpy_data(path: Path, zone_folder: Path, zones: gpd.GeoDataFrame) -> None:
        """Process RapidXPy data for conversion."""
        logger.debug("Processing RapidXPy network data for RapidEx...")
        DataFormatConverter._process_network_data(path)
        logger.debug("Processing RapidXPy travel times for RapidEx...")
        DataFormatConverter._process_traveltimes(path)
        logger.debug("Processing RapidXPy zones/centroids for RapidEx...")
        DataFormatConverter._process_zones_and_centroids(path, zone_folder, zones)
        logger.debug("Processing RapidXPy custom solution for RapidEx...")
        DataFormatConverter._process_custom_solution(path, zone_folder)
        logger.debug("Processing RapidXPy results for RapidEx...")
        DataFormatConverter._process_results(path, zone_folder)

    @staticmethod
    def _process_network_data(path: Path) -> None:
        """Process and save network data for RapidEx."""
        network_folder = path / 'Network'
        logger.debug(f"Processing network data from {network_folder}")
        try:
            edges_file = network_folder / 'edges.geojson'
            if edges_file.exists():
                logger.debug("Processing edges.geojson...")
                edges = gpd.read_file(edges_file)
                edges.rename(columns={'EdgeID': 'edgeID'}, inplace=True, errors='ignore')
                edges = DataFormatConverter.list2str(edges)
                edges.to_file(path/'rapidex/edges.geojson', driver='GeoJSON')
                edges.to_file(path/'rapidex/edges.gpkg', driver='GPKG')
                logger.debug("Saved edges.geojson and edges.gpkg to rapidex/")
            else:
                logger.warning(f"{edges_file} not found. Skipping edges processing.")

            # Process nodes
            nodes_file = network_folder / 'nodes.geojson'
            if nodes_file.exists():
                logger.debug("Processing nodes.geojson...")
                nodes = gpd.read_file(nodes_file)
                nodes.rename(columns={'NodeID': 'nodeID'}, inplace=True, errors='ignore')
                nodes = DataFormatConverter.list2str(nodes)
                nodes.to_file(path/'rapidex/nodes.geojson', driver='GeoJSON')
                nodes.to_file(path/'rapidex/nodes.gpkg', driver='GPKG')
                logger.debug("Saved nodes.geojson and nodes.gpkg to rapidex/")
            else:
                logger.warning(f"{nodes_file} not found. Skipping nodes processing.")

            # Process network CSV
            network_csv = network_folder / 'Network.csv'
            if network_csv.exists():
                logger.debug("Processing Network.csv...")
                network = pd.read_csv(network_csv)
                network.rename(columns={
                    'length': 'length (m)',
                    'capacity': 'capacity (veh/hr)',
                    'fft': 'Free Flow Time (min)',
                    'alpha': 'B',
                    'beta': 'Power',
                    'etype': 'Type'
                }, inplace=True)
                if 'Type' in network.columns:
                    network['Type'] = network['Type'].apply(lambda x: 'A' if x == 'Arterial' else ('F' if x == 'Freeway' else x))
                else:
                    logger.warning("'Type' column not found in Network.csv. Skipping type conversion.")
                network.to_csv(path/'rapidex/net.csv', index=False)
                logger.debug("Saved net.csv to rapidex/")
            else:
                logger.warning(f"{network_csv} not found. Skipping network CSV processing.")

        except Exception as e:
            logger.exception(f"Error processing network data for RapidEx.-> {e}")
            raise

    @staticmethod
    def _process_traveltimes(path: Path) -> None:
        """Process travel times data for RapidEx."""
        traveltimes_rpy_folder = path / 'Traveltimes'
        if not traveltimes_rpy_folder.exists():
            logger.warning(f"RapidXPy Traveltimes folder {traveltimes_rpy_folder} not found. Skipping.")
            return

        logger.debug(f"Processing travel times from {traveltimes_rpy_folder}")
        processed_count = 0
        try:
            for tt_file in traveltimes_rpy_folder.glob('*.csv'):
                logger.debug(f"Processing travel time file: {tt_file.name}")
                tt = pd.read_csv(tt_file)
                tt.rename(columns={'from': 'From', 'to': 'To'}, inplace=True, errors='ignore')

                model_name = tt_file.stem
                # Create destination folder structure
                dest_folder = path / 'rapidex/traveltimes' / model_name
                dest_folder.mkdir(parents=True, exist_ok=True)
                tempfol = dest_folder / '.traveltime'
                tempfol.mkdir(parents=True, exist_ok=True)
                # Save the renamed file
                output_path = dest_folder / 'link_TT.csv'
                tt.to_csv(output_path, index=False)
                logger.debug(f"Saved processed travel times to {output_path}")
                processed_count += 1
            logger.info(f"Processed {processed_count} travel time files for RapidEx.")
        except Exception as e:
            logger.exception(f"Error processing travel times for RapidEx. -> {e}") 
            raise

    @staticmethod
    def _process_zones_and_centroids(path: Path, zone_folder: Path, zones: gpd.GeoDataFrame) -> None:
        """Process zones and centroids data for RapidEx."""
        logger.debug(f"Processing zones and centroids from {zone_folder}")
        try:
            # Process zones
            logger.debug("Processing zones...")
            zones.rename(columns={'ZoneID': 'fishnetID'}, inplace=True, errors='ignore')
            DataFormatConverter.list2str(zones).to_file(
                path/'rapidex/aggrid_polygon.geojson', driver='GeoJSON'
            )
            logger.debug("Saved aggrid_polygon.geojson to rapidex/")

            # Process centroids
            centroids_file = path / zone_folder / 'Centroids.geojson'
            if centroids_file.exists():
                logger.debug("Processing centroids...")
                centroids = gpd.read_file(centroids_file)
                centroids.rename(columns={'ZoneID': 'fishnetID', 'NodeID': 'osmid'}, inplace=True, errors='ignore')
                centroids_df = pd.DataFrame(centroids.drop(columns='geometry', errors='ignore'))
                output_path = path / 'rapidex/centroid.csv'
                centroids_df.to_csv(output_path, index=False)
                logger.debug("Saved centroid.csv to rapidex/")
            else:
                logger.warning(f"Centroids file {centroids_file} not found. Skipping centroids processing.")

        except Exception as e:
            logger.exception(f"Error processing zones and centroids for RapidEx. {e}")
            raise

    @staticmethod
    def _process_custom_solution(path: Path, zone_folder: Path) -> None:
        """Process custom solution data for RapidEx."""
        custom_solution_file = path / zone_folder / 'CustomInitialSolution.csv'
        if not custom_solution_file.exists():
            logger.info(f"CustomInitialSolution.csv not found in {zone_folder}. Skipping.")
            return
        try:
            logger.debug(f"Processing {custom_solution_file}...")
            custom_initial_solution = pd.read_csv(custom_solution_file)
            if list(custom_initial_solution.columns) == ['ZoneID', 'Productions', 'Attractions']:
                custom_initial_solution.columns = ['Centroid', 'Proportion', 'Proportion'] # Rename as per original code
                output_path = path / 'rapidex/customInitialSolution.csv'
                custom_initial_solution.to_csv(output_path, index=False)
                logger.debug(f"Saved custom solution to {output_path}")
            else:
                logger.warning(f"Unexpected columns in {custom_solution_file}: {list(custom_initial_solution.columns)}. Expected ['ZoneID', 'Productions', 'Attractions']. Skipping rename and save.")

        except Exception as e:
            logger.exception(f"Error processing custom solution file {custom_solution_file}. -> {e}")

    @staticmethod
    def process_results_file(file: Path, dest_path: Path, file_type: str) -> None:
        """Process a results file based on its type for RapidEx."""
        logger.debug(f"Processing results file: {file.name} (type: {file_type}) -> {dest_path}")
        try:
            df = pd.read_csv(file)
            processed = False

            if 'linkTT' in file_type:
                logger.debug("Processing as linkTT...")
                required_cols = ['From', 'To', 'Volume', 'EstimatedTT']
                if all(col in df.columns for col in required_cols):
                    df1 = df[required_cols].copy()
                    df1.rename(columns={
                        'From': 'from',
                        'To': 'to',
                        'Volume': 'volume',
                        'EstimatedTT': 'TT'
                    }, inplace=True)
                    df1.to_csv(dest_path / file.name, index=False)
                    logger.debug(f"Saved processed linkTT to {dest_path / file.name}")
                    df.to_csv(dest_path / 'link_emissions.csv', index=False)
                    logger.debug(f"Saved link_emissions.csv to {dest_path}")
                    processed = True
                else:
                    logger.warning(f"Missing required columns for linkTT processing in {file.name}. Skipping specific processing.")

            elif 'ODTT' in file_type:
                logger.debug("Processing as ODTT...")
                df.rename(columns={
                    'Origin': 'origin',
                    'Destination': 'destination'
                }, inplace=True, errors='ignore')
                df.to_csv(dest_path / file.name, index=False)
                logger.debug(f"Saved processed ODTT to {dest_path / file.name}")
                processed = True

            elif 'zone_emissions' in file_type:
                logger.debug("Processing as zone_emissions...")
                df.drop_duplicates(subset=['ZoneID'], keep='last', inplace=True)
                df.rename(columns={'ZoneID': 'fishnetid'}, inplace=True, errors='ignore')
                df.to_csv(dest_path / 'zone_emissions.csv', index=False)
                logger.debug(f"Saved processed zone_emissions.csv to {dest_path}")
                processed = True

            elif 'LinkResults' in file_type:
                logger.debug("Processing as LinkResults...")
                required_cols = ['From', 'To', 'Volume', 'EstimatedTT']
                if all(col in df.columns for col in required_cols):
                    df_processed = df[required_cols].copy()
                    df_processed.rename(columns={
                        'From': 'from',
                        'To': 'to',
                        'EstimatedTT': 'TT'
                    }, inplace=True)
                    try:
                        elite_num_str = file.stem.split('Elite')[1].split('Link')[0]
                        elite_num = int(elite_num_str) + 1
                        new_filename = f'Elite{elite_num}LinkResults.csv'
                        df_processed.to_csv(dest_path / new_filename, index=False)
                        logger.debug(f"Saved processed LinkResults to {dest_path / new_filename}")
                        processed = True
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Could not parse Elite number from {file.name}: {e}. Skipping specific processing.")
                else:
                    logger.warning(f"Missing required columns for LinkResults processing in {file.name}. Skipping specific processing.")

            elif 'ODResults' in file_type:
                logger.debug("Processing as ODResults...")
                df.rename(columns={
                    'EstimatedTT': 'TT',
                    'ObservedTT': 'TTobs'
                }, inplace=True, errors='ignore')
                try:
                    elite_num_str = file.stem.split('Elite')[1].split('OD')[0]
                    elite_num = int(elite_num_str) + 1
                    new_filename = f'Elite{elite_num}ODResults.csv'
                    df.to_csv(dest_path / new_filename, index=False)
                    logger.debug(f"Saved processed ODResults to {dest_path / new_filename}")
                    processed = True
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse Elite number from {file.name}: {e}. Skipping specific processing.")

            elif 'Stats' in file_type:
                logger.debug("Processing as Stats...")
                try:
                    elite_num_str = file.stem.split('Elite')[1].split('Stats')[0]
                    elite_num = int(elite_num_str) + 1
                    new_filename = f'Elite{elite_num}Validation.csv'
                    df.to_csv(dest_path / new_filename, index=False)
                    logger.debug(f"Saved processed Stats to {dest_path / new_filename}")
                    processed = True
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse Elite number from {file.name}: {e}. Skipping specific processing.")

            if not processed:
                logger.debug(f"No specific processor applied to {file.name}. Copying file.")
                output_filepath = dest_path / file.name
                file.rename(output_filepath)
                logger.debug(f"Copied file to {output_filepath}")

        except Exception as e:
            logger.error(f"Error processing results file {file.name}: {e}", exc_info=True)

    @staticmethod
    def _process_results(path: Path, zone_folder: Path) -> None:
        """Process results data for RapidEx."""
        rpath = path / zone_folder / 'Results'
        if not rpath.exists() or not rpath.is_dir():
            logger.warning(f"Results directory {rpath} not found. Skipping results processing.")
            return

        logger.debug(f"Processing results from {rpath}...")
        processed_models = 0
        for fol in rpath.iterdir():
            if fol.is_dir():
                logger.debug(f"Processing model results directory: {fol.name}")
                newr_path = path / 'rapidex' / 'models' / fol.name
                newr_path.mkdir(parents=True, exist_ok=True)
                processed_subfolders = 0

                for f in fol.iterdir():
                    if not f.is_dir():
                        logger.debug(f"Skipping non-directory item in model results: {f.name}")
                        continue

                    if f.name == 'Assignments':
                        logger.debug("Processing Assignments subfolder...")
                        new_ass_path = newr_path / 'assignments'
                        new_ass_path.mkdir(parents=True, exist_ok=True)
                        for item in f.iterdir():
                            if item.is_dir():
                                logger.debug(f"Processing subdirectory within Assignments: {item.name}")
                                for file in item.iterdir():
                                    if file.is_file():
                                        DataFormatConverter.process_results_file(file, new_ass_path, file.name)
                            elif item.is_file():
                                DataFormatConverter.process_results_file(item, new_ass_path, item.name)
                        processed_subfolders += 1

                    elif f.name == 'Estimation':
                        logger.debug("Processing Estimation subfolder...")
                        new_est_path = newr_path / 'estimates'
                        new_est_path.mkdir(parents=True, exist_ok=True)
                        for item in f.iterdir():
                            if item.is_dir():
                                logger.debug(f"Processing subdirectory within Estimation: {item.name}")
                                for file in item.iterdir():
                                    if file.is_file():
                                        DataFormatConverter.process_results_file(file, new_est_path, file.name)
                            elif item.is_file():
                                DataFormatConverter.process_results_file(item, new_est_path, item.name)
                        processed_subfolders += 1
                    else:
                        logger.debug(f"Skipping unknown subfolder in model results: {f.name}")

                logger.debug(f"Finished processing {processed_subfolders} subfolders for model {fol.name}")
                processed_models += 1
        logger.info(f"Processed results for {processed_models} models.")

class Visualization:
    """Handles visualization setup."""

    @staticmethod
    def run_flask_app():
        """Run Flask application."""
        try:
            from ..visualize import app
            logger.info("Starting Flask development server for visualization...")
            app.run(debug=True, use_reloader=False)
            logger.info("Flask server stopped.")
        except ImportError:
            logger.exception("Could not import Flask app from ..visualize. Ensure it's installed and configured.")
            raise
        except Exception as e:
            logger.exception(f"Error running Flask application: {e}")
            raise

    @staticmethod
    def run_rapidxpy_vis(path: Optional[str],
                    zone_folder: Optional[Path],
                    ex_date: str,
                    aore: str) -> None:
        """Set up and run RapidXPy visualization."""
        logger.info("Setting up environment for RapidXPy visualization...")
        try:
            if path is None:
                if zone_folder is None:
                    logger.error("Either 'path' or 'zone_folder' must be provided for visualization.")
                    raise ValueError("Missing path information for visualization.")
                zone_folder_abs = zone_folder.resolve()
                data_path = str(zone_folder_abs.parent.parent)
                region = str(zone_folder_abs.parent.name)
                zonefolder_name = str(zone_folder_abs.name)
                model = str(ex_date)
                aore_val = aore

                logger.debug(f"Setting environment variables: DATA_PATH={data_path}, REGION={region}, ZONEFOLDER={zonefolder_name}, MODEL={model}, AORE={aore_val}")
                os.environ.update({
                    'DATA_PATH': data_path,
                    'REGION': region,
                    'ZONEFOLDER': zonefolder_name,
                    'MODEL': model,
                    'AORE': aore_val
                })
            else:
                logger.debug(f"Setting environment variable: DATA_PATH={path}")
                os.environ['DATA_PATH'] = str(path)

            # Start Flask server in separate thread
            logger.info("Starting Flask server in a background thread...")
            flask_thread = threading.Thread(
                target=Visualization.run_flask_app, daemon=True # Use daemon thread
            )
            flask_thread.start()

            # Open browser after short delay
            logger.info("Waiting for Flask server to start...")
            time.sleep(2)
            url = 'http://127.0.0.1:5000/'
            logger.info(f"Opening web browser at {url}")
            webbrowser.open(url)

            logger.info("Visualization running. Press Ctrl+C in the terminal to stop the Flask server.")
            # flask_thread.join() # If join is used, execution waits here until Flask stops.
            # Instead, let the main script potentially exit, Flask runs as daemon.
            # Or, implement a cleaner shutdown mechanism.
            while flask_thread.is_alive():
                time.sleep(1)

        except Exception as e:
            logger.exception(f"Error setting up or running visualization: {e}")
            raise


def add_edge_travel_time(graph: nx.DiGraph,
                        estimated_tt: pd.DataFrame,
                        network_type: str,
                        walk_speed: Optional[float] = None,
                        bike_speed: Optional[float] = None) -> nx.DiGraph:
    """Add travel time ('cost') attribute to graph edges."""
    logger.info(f"Adding edge travel times for network type: {network_type}")
    edges_processed = 0
    edges_missing_tt = 0

    if network_type in ['walk', 'bike']:
        speed = walk_speed if network_type == 'walk' else bike_speed
        if speed is None or speed <= 0:
            logger.error(f"Invalid speed provided for {network_type}: {speed}")
            raise ValueError(f"Valid speed required for {network_type}")
        logger.debug(f"Using speed: {speed} m/s for {network_type}")
        for u, v, data in graph.edges(data=True):
            try:
                if 'x' not in graph.nodes[u] or 'y' not in graph.nodes[u] or \
                'x' not in graph.nodes[v] or 'y' not in graph.nodes[v]:
                    logger.warning(f"Missing coordinates for nodes {u} or {v}. Cannot calculate distance/cost for edge ({u},{v}). Setting cost to infinity.")
                    data['cost'] = float('inf')
                    continue

                distance = AssignmentCalculator.haversine(
                    graph.nodes[u]['x'], graph.nodes[u]['y'],
                    graph.nodes[v]['x'], graph.nodes[v]['y'],
                    unit='meter'
                )
                data['cost'] = distance / speed
                edges_processed += 1
            except Exception as e:
                logger.error(f"Error calculating cost for edge ({u},{v}) using haversine/speed: {e}", exc_info=True)
                data['cost'] = float('inf')

    else:
        logger.debug("Using estimated_tt DataFrame for drive network costs.")
        tt_dict = {(row['From'], row['To']): row['EstimatedTT']
                for _, row in estimated_tt.iterrows()}
        logger.debug(f"Created travel time lookup dictionary with {len(tt_dict)} entries.")

        for u, v, data in graph.edges(data=True):
            cost = tt_dict.get((u, v))
            if cost is not None:
                data['cost'] = cost
                edges_processed += 1
            else:
                logger.warning(f"No estimated travel time found for edge ({u},{v}). Setting cost to infinity.")
                data['cost'] = float('inf')
                edges_missing_tt += 1

    if edges_missing_tt > 0:
        logger.warning(f"{edges_missing_tt} edges were missing travel time data in the provided DataFrame.")
    logger.info(f"Finished adding travel times. Processed {edges_processed} edges.")
    return graph


def get_a3cc(nodes: gpd.GeoDataFrame) -> str:
    """Get alpha-3 country code from nodes location."""
    logger.debug("Attempting to determine alpha-3 country code...")
    try:
        if len(nodes) >= 2:
            sample_points = [
                (nodes.iloc[0].geometry.y, nodes.iloc[0].geometry.x),
                (nodes.iloc[-1].geometry.y, nodes.iloc[-1].geometry.x)
            ]
        elif len(nodes) == 1:
            sample_points = [
                (nodes.iloc[0].geometry.y, nodes.iloc[0].geometry.x)]
        else:
            logger.error(
                "Cannot determine country code: nodes GeoDataFrame is empty.")
            raise ValueError("Nodes GeoDataFrame is empty.")

        logger.debug(
            f"Using sample points for reverse geocoding: {sample_points}")
        # Get alpha-2 code from reverse geocoding
        geo_results = reverse_geocode.search(sample_points)
        if not geo_results:
            logger.error("Reverse geocoding failed to return results.")
            raise ValueError("Reverse geocoding failed.")

        a2cc = geo_results[0]['country_code']
        logger.debug(f"Reverse geocoding result (alpha-2): {a2cc}")

        # Convert to alpha-3
        country = pycountry.countries.get(alpha_2=a2cc)
        if country is None:
            logger.error(
                f"Could not find country information for alpha-2 code: {a2cc}")
            raise ValueError(f"Invalid alpha-2 country code: {a2cc}")

        a3cc = country.alpha_3.lower()
        logger.info(f"Determined alpha-3 country code: {a3cc}")
        return a3cc
    except Exception as e:
        logger.exception(f"Error determining country code: {e}")
        raise

def get_validation_stats(individual: Dict) -> Tuple:
    """
    Calculate validation statistics for GA/assignment output.

    Args:
        individual: Dictionary containing assignment results
                    (Graph, EODTT, SPEdges, Gene, TSTT, TotalDemand, Fitness).

    Returns:
        Tuple containing various validation statistics.
    """
    # set logging level to warning to avoid excessive debug output
    logger.setLevel(logging.WARNING)
    logger.info("Calculating validation statistics...")
    # logger.debug(f"Input individual keys: {individual.keys()}")

    try:
        sumDist = 0.0
        sumFFTT = 0.0
        FFTT = {}

        # Check required keys
        required_keys = ['Graph', 'EODTT', 'SPEdges', 'Gene', 'TSTT', 'TotalDemand', 'Fitness']
        if not all(key in individual for key in required_keys):
            missing = [key for key in required_keys if key not in individual]
            logger.error(f"Missing required keys in 'individual' dict for validation: {missing}")
            raise KeyError(f"Missing required keys: {missing}")

        logger.debug("Calculating demand-weighted distance and total FFTT...")
        processed_trips = 0
        for trip, estimated_tt in individual['EODTT'].items():
            try:
                path = individual['SPEdges'].get(trip)
                demand = individual['Gene'].get(trip, 0)

                if path is None:
                    logger.warning(f"Shortest path not found for OD pair {trip}. Skipping.")
                    continue

                dist = AssignmentCalculator.get_dist(individual['Graph'], path)
                sumDist += dist * demand

                fftt_val = AssignmentCalculator.get_fftt(individual['Graph'], path)
                FFTT[trip] = fftt_val
                sumFFTT += fftt_val * demand
                processed_trips += 1

            except KeyError as e:
                logger.warning(f"KeyError processing trip {trip}: {e}. Skipping trip.")
                continue
            except Exception as e:
                logger.error(f"Error processing trip {trip}: {e}", exc_info=True)
                continue
        logger.debug(f"Processed {processed_trips} trips for distance and FFTT.")

        # Avoid division by zero
        total_demand = individual['TotalDemand']
        if total_demand <= 0:
            logger.warning("TotalDemand is zero or negative. Setting per-demand stats to zero.")
            timeperdem = 0.0
            distperdem = 0.0
        else:
            timeperdem = individual['TSTT'] / total_demand
            distperdem = sumDist / total_demand

        if sumFFTT <= 0:
            logger.warning("Sum of Free Flow Travel Times (FFTT) is zero or negative. Setting ttpercentFFTT to zero.")
            ttpercentFFTT = 0.0
        else:
            sum_eodtt = sum(individual['EODTT'].values())
            ttpercentFFTT = 100 * (1-(sum_eodtt / sumFFTT)) if sumFFTT > 0 else 0


        logger.debug("Calculating edge-based statistics (congestion, VKT)...")
        edgelengths = 0.0
        nonzeroedgelengths = 0.0
        wLC, wVC, vKT = 0.0, 0.0, 0.0
        nzwlc, nzwvc = 0.0, 0.0

        processed_edges = 0
        for u, v, data in individual['Graph'].edges(data=True):
            try:
                length = data.get('length', 0.0)
                if length > 0:
                    edgelengths += length
                    cost = data.get('cost', 0.0)
                    fft = data.get('FFT', 0.0)
                    flow = data.get('flow', 0.0)
                    capacity = data.get('capacity', 0.0)

                    # Avoid division by zero for FFT and capacity
                    cost_ratio = (cost / fft) if fft > 1e-9 else 1.0
                    vc_ratio = (flow / capacity) if capacity > 1e-9 else 0.0

                    wLC += length * cost_ratio
                    wVC += length * vc_ratio

                    if flow > 1e-9:
                        nonzeroedgelengths += length
                        nzwlc += length * cost_ratio
                        nzwvc += length * vc_ratio
                        vKT += length * flow
                processed_edges += 1
            except Exception as e:
                logger.error(f"Error processing edge ({u},{v}) for stats: {e}", exc_info=True)
                continue
        logger.debug(f"Processed {processed_edges} edges for stats.")

        # Calculate final edge stats, avoiding division by zero
        wlcpel = wLC / edgelengths if edgelengths > 0 else 0.0 # Weighted Link Congestion per Edge Length
        wvcpel = wVC / edgelengths if edgelengths > 0 else 0.0 # Weighted V/C per Edge Length
        nzwlcpel = nzwlc / nonzeroedgelengths if nonzeroedgelengths > 0 else 0.0
        nzwvcpel = nzwvc / nonzeroedgelengths if nonzeroedgelengths > 0 else 0.0

        # Format results as per original code
        results = (
            individual['TSTT'] / 60.0, # TSTT in hours.
            individual['Fitness'],
            individual['TotalDemand'],
            round(timeperdem, 4),
            round(distperdem, 4),
            round(ttpercentFFTT, 4),
            round(wlcpel, 4),
            round(wvcpel, 4),
            round(vKT / 1000.0, 4), # VKT in thousands
            nzwlcpel if nonzeroedgelengths > 0 else 'zero flow on all edges',
            nzwvcpel if nonzeroedgelengths > 0 else 'zero flow on all edges',
            FFTT
        )
        logger.info("Finished calculating validation statistics.")
        logger.debug(f"Validation results: {results}")
        return results

    except KeyError as e:
        logger.error(f"Missing key during validation statistics calculation: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.exception(f"Error calculating validation statistics: {e}")
        raise


def save_tt_emissions(network_graph: nx.DiGraph,
                    edges_file: str,
                    save_folder: str,
                    zone_file: str,
                    link_index_ordering: List[Tuple],
                    save_emission: bool = True) -> None:
    """Save travel times and optionally emissions data."""
    logger.info(f"Saving travel time and emissions data to folder: {save_folder}")
    save_folder_path = Path(save_folder)
    save_folder_path.mkdir(parents=True, exist_ok=True) # Ensure folder exists

    try:
        # Convert graph to DataFrame
        logger.debug("Converting graph edges to DataFrame...")
        if not network_graph.edges:
            logger.warning("Network graph has no edges. Cannot save travel times.")
            return
        graph_df = nx.to_pandas_edgelist(network_graph)
        # Select and rename columns
        graph_df = graph_df[['source', 'target', 'flow', 'cost', 'length']].copy()
        graph_df.rename(columns={
            'source': 'From',
            'target': 'To',
            'flow': 'Volume',
            'cost': 'EstimatedTT'
        }, inplace=True)
        logger.debug(f"Created DataFrame with {len(graph_df)} edges.")

        # Round values
        graph_df = graph_df.round({'Volume': 2, 'EstimatedTT': 2})

        # Reorder based on link ordering if provided
        if link_index_ordering:
            logger.debug("Reordering DataFrame based on link_index_ordering...")
            try:
                graph_df['temp'] = list(zip(graph_df['From'], graph_df['To']))
                graph_df.set_index('temp', inplace=True)
                graph_df = graph_df.reindex(link_index_ordering)
                # Handle edges in ordering but not in graph (will have NaNs)
                missing_in_graph = graph_df['Volume'].isnull().sum()
                if missing_in_graph > 0:
                    logger.warning(f"{missing_in_graph} links from ordering were not found in the graph results.")
                graph_df.reset_index(inplace=True)
                if 'index' in graph_df.columns and isinstance(graph_df['index'].iloc[0], tuple):
                    graph_df.drop(columns=['index'], inplace=True)
                elif 'temp' in graph_df.columns:
                    graph_df.drop(columns=['temp'], inplace=True, errors='ignore')


            except Exception as e:
                logger.error(f"Error reindexing DataFrame: {e}. Proceeding without reordering.", exc_info=True)
                if 'temp' in graph_df.columns:
                    graph_df.drop(columns=['temp'], inplace=True, errors='ignore')
        else:
            logger.debug("No link_index_ordering provided. Saving in arbitrary order.")


        logger.debug("Calculating speed and CO2 emissions...")
        graph_df['Speed_mps'] = graph_df.apply(
            lambda x: (x['length'] / (x['EstimatedTT'] * 60.0)) if x['EstimatedTT'] > 1e-9 else 0.0,
            axis=1
        )
        graph_df['Speed_mph'] = graph_df['Speed_mps'] * 2.23694
        graph_df['Speed_mph_capped'] = graph_df['Speed_mph'].clip(upper=100)

        graph_df['CO2_g_per_mile'] = graph_df['Speed_mph_capped'].apply(
            lambda x: np.exp(
                7.613 - 0.138565*x + 0.0039151*x**2 -
                0.000049519*x**3 + 0.0000002386*x**4
            ) if x > 0 else 0
        )

        meters_to_miles = 1 / 1609.34
        grams_to_tons = 1 / 1_000_000
        graph_df['CO2'] = (graph_df['CO2_g_per_mile'] * graph_df['length'] * meters_to_miles *
                                graph_df['Volume'] * grams_to_tons)
        logger.debug("Finished calculating speed and CO2 emissions.")

        linktt_filepath = save_folder_path / 'linkTT.csv'
        logger.info(f"Saving link travel times to {linktt_filepath}...")
        # Select columns to save for linkTT
        linktt_cols = ['From', 'To', 'Volume', 'EstimatedTT', 'length', 'CO2']
        graph_df[linktt_cols].to_csv(linktt_filepath, index=False)
        logger.info("Link travel times saved.")

        if save_emission:
            logger.info("Saving zonal emissions...")
            save_emissions(
                graph_df, edges_file, zone_file, save_folder
            )
            logger.info("Zonal emissions saved.")
        else:
            logger.info("Skipping zonal emissions save as per request.")

    except Exception as e:
        logger.exception(f"Error saving travel time and emissions data: {e}")
        raise


def save_emissions(graph_df: pd.DataFrame,
                edges_file: str,
                zone_file: str,
                save_folder: str) -> None:
    """Save emissions data aggregated by zone."""
    logger.debug("Starting zonal emissions aggregation...")
    save_folder_path = Path(save_folder)

    try:
        # Load edges and zones GeoDataFrames
        logger.debug(f"Loading edges from {edges_file}")
        edges = gpd.read_file(edges_file)
        logger.debug(f"Loading zones from {zone_file}")
        zones = gpd.read_file(zone_file)

        # Ensure required columns exist
        if 'CO2' not in graph_df.columns:
            logger.error("'CO2' column not found in graph_df. Cannot save emissions.")
            raise ValueError("'CO2' column not found in graph_df.")
        if 'ZoneID' not in zones.columns:
            logger.error("'ZoneID' column not found in zones file. Cannot aggregate emissions.")
            raise ValueError("'ZoneID' column not found in zones file.")
        if 'from' not in edges.columns or 'to' not in edges.columns:
            logger.error("'from' or 'to' column not found in edges file. Cannot merge emissions.")
            raise ValueError("'from' or 'to' column not found in edges file.")

        # Merge edges with graph emissions data
        logger.debug("Merging edge geometries with emissions data...")
        # Select only necessary columns for merge
        emissions_to_merge = graph_df[['From', 'To', 'CO2']].copy()
        edges_graph = pd.merge(
            edges,
            emissions_to_merge,
            left_on=['from', 'to'],
            right_on=['From', 'To'],
            how='left'
        )
        edges_graph.drop_duplicates(subset=['from', 'to'], inplace=True)
        edges_graph['CO2'].fillna(0, inplace=True)
        logger.debug(f"Merged emissions onto {len(edges_graph)} edges.")

        # Ensure CRS match before spatial join if necessary
        if edges.crs != zones.crs:
            logger.warning(f"Edges CRS ({edges.crs}) and Zones CRS ({zones.crs}) differ. Reprojecting edges to match zones.")
            edges_graph = edges_graph.to_crs(zones.crs)

        # Perform spatial join (find which zone each edge is 'within')
        # Consider using 'intersects' if edges might cross boundaries
        logger.debug("Performing spatial join between zones and edges...")
        edges_in_zones_join = gpd.sjoin(
            zones[['ZoneID', 'Population', 'Area', 'geometry']], # Select needed zone columns
            edges_graph[['from', 'to', 'CO2', 'geometry']], # Select needed edge columns
            how='inner', # Only keep zones containing edges
            predicate='contains' # Find edges contained within zones
        )
        logger.debug(f"Spatial join resulted in {len(edges_in_zones_join)} edge segments within zones.")

        # Group by ZoneID and sum CO2
        logger.debug("Aggregating CO2 emissions by ZoneID...")
        zonal_emissions = edges_in_zones_join.groupby('ZoneID').agg(
            CO2=('CO2', 'sum'),
            Population=('Population', 'first'),
            Area=('Area', 'first')
        ).reset_index()
        logger.debug(f"Aggregated emissions for {len(zonal_emissions)} zones.")

        # Calculate per capita and per area emissions, handle division by zero
        logger.debug("Calculating per capita and per area emissions...")
        zonal_emissions['CO2pcap'] = zonal_emissions.apply(
            lambda row: (row['CO2'] / row['Population']) if row['Population'] > 0 else 0,
            axis=1
        )
        zonal_emissions['CO2psqkm'] = zonal_emissions.apply(
            lambda row: (row['CO2'] / row['Area']) if row['Area'] > 0 else 0,
            axis=1
        )

        # Save results
        output_filepath = save_folder_path / 'zone_emissions.csv'
        logger.info(f"Saving zonal emissions results to {output_filepath}...")
        zonal_emissions.to_csv(output_filepath, index=False)
        logger.info("Zonal emissions saved successfully.")

    except FileNotFoundError as e:
        logger.error(f"File not found during emissions saving: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.exception(f"Error saving emissions data: {e}")
        raise


def bpr_function(x: Dict, tmp_flow: float) -> Tuple[float, float]:
    """
    Calculate BPR (Bureau of Public Roads) function values.

    Args:
        x: Dictionary of edge attributes (FFT, alpha, beta, capacity).
        tmp_flow: Temporary flow value for calculation.

    Returns:
        Tuple containing (cost, cost_derivative).
    """
    required_keys = ['FFT', 'alpha', 'beta', 'capacity']
    if not all(key in x for key in required_keys):
        raise KeyError(f"Missing required keys for BPR function: { [k for k in required_keys if k not in x] }")

    fft = x['FFT']
    alpha = x['alpha']
    beta = x['beta']
    capacity = x['capacity']

    if capacity <= 1e-9:
        cost = fft if tmp_flow <= 1e-9 else float('inf')
        derivative = 0.0
    else:
        flow_term = tmp_flow / capacity
        try:
            cost = fft * (1.0 + alpha * math.pow(flow_term, beta))

            # Calculate derivative: d(cost)/d(flow)
            if beta == 0 or tmp_flow <= 1e-9:
                derivative = 0.0
            else:
                derivative = (fft * alpha * beta *
                            math.pow(tmp_flow, beta - 1.0) /
                            math.pow(capacity, beta))
        except ValueError as e:
            logger.error(f"Math error in BPR calculation: flow={tmp_flow}, capacity={capacity}, beta={beta}. Error: {e}")
            cost = float('inf')
            derivative = 0.0

    logger.debug(f"BPR input: FFT={fft}, alpha={alpha}, beta={beta}, cap={capacity}, flow={tmp_flow} -> cost={cost}, dcost={derivative}")
    return cost, derivative


def get_od_stats(graph: nx.DiGraph,
                from_cent: Dict,
                to_cent: Dict,
                compute_sptt: bool = False) -> Union[Dict, Tuple[Dict, Dict, Dict]]:
    """Calculate OD statistics (average distance, optionally shortest paths and TTs)."""
    logger.debug(f"Calculating OD statistics. Compute SP/TT: {compute_sptt}")
    od_avg_dist = {}
    od_sp = {} if compute_sptt else None
    od_tt = {} if compute_sptt else None
    processed_origins = 0
    unreachable_pairs = 0

    for ozone, onode in from_cent.items():
        logger.debug(f"Processing origin zone {ozone} (node {onode}) for OD stats...")
        try:
            tt_d, path_d = nx.single_source_dijkstra(
                graph, onode, weight='cost'
            )
        except nx.NodeNotFound:
            logger.warning(f"Origin node {onode} not found in graph. Skipping origin {ozone} for OD stats.")
            continue
        except Exception as e:
            logger.error(f"Dijkstra failed for origin node {onode}: {e}", exc_info=True)
            continue

        processed_destinations = 0
        for dzone, dnode in to_cent.items():
            if ozone == dzone:
                continue

            od_pair = (ozone, dzone)
            if dnode in path_d:
                try:
                    path = path_d[dnode]
                    dist = AssignmentCalculator.get_dist(graph, path)
                    od_avg_dist[od_pair] = dist

                    if compute_sptt:
                        od_sp[od_pair] = path
                        od_tt[od_pair] = tt_d[dnode]
                    processed_destinations += 1
                except KeyError as e:
                    logger.error(f"Error calculating distance for reachable OD pair {od_pair}: {e}", exc_info=True)
                    od_avg_dist[od_pair] = -1.0 # Indicate error
                    if compute_sptt:
                        od_sp[od_pair] = []
                        od_tt[od_pair] = -1.0
                except Exception as e:
                    logger.error(f"Unexpected error processing OD pair {od_pair}: {e}", exc_info=True)
                    od_avg_dist[od_pair] = -1.0
                    if compute_sptt:
                        od_sp[od_pair] = []
                        od_tt[od_pair] = -1.0

            else:
                logger.debug(f'No path found between OD pair {od_pair}')
                unreachable_pairs += 1
                od_avg_dist[od_pair] = float('inf')
                if compute_sptt:
                    od_sp[od_pair] = []
                    od_tt[od_pair] = float('inf')

        logger.debug(f"Processed {processed_destinations} destinations for origin {ozone}.")
        processed_origins += 1

    logger.debug(f"Finished calculating OD stats for {processed_origins} origins.")
    if unreachable_pairs > 0:
        logger.warning(f"{unreachable_pairs} OD pairs were found to be unreachable.")

    if compute_sptt:
        logger.debug("Returning OD shortest paths, travel times, and average distances.")
        return od_sp, od_tt, od_avg_dist
    else:
        logger.debug("Returning OD average distances.")
        return od_avg_dist

def flow_comparison(observed: pd.DataFrame, estimated: pd.DataFrame) -> None:
    """Compare observed total flow with estimated total flow."""
    logger.info("Performing total flow comparison...")
    try:
        # Ensure consistent column names for merging
        observed_renamed = observed.rename(columns={'from': 'From', 'to': 'To', 'demand': 'Observed_Flow'}, errors='ignore')
        estimated_renamed = estimated.rename(columns={'Volume': 'Estimated_Flow'}, errors='ignore')

        # Merge observed and estimated data on 'From' and 'To'
        merged_df = pd.merge(
            observed_renamed[['From', 'To', 'Observed_Flow']],
            estimated_renamed[['From', 'To', 'Estimated_Flow']],
            on=['From', 'To'],
            how='inner'
        )
        merged_df.drop_duplicates(subset=['From', 'To'], inplace=True)
        logger.debug(f"Merged observed and estimated flows for {len(merged_df)} links.")

        merged_df.dropna(subset=['Observed_Flow'], inplace=True)
        merged_df = merged_df[merged_df['Observed_Flow'] > 0]
        logger.debug(f"Number of links after filtering zero/NaN observed flow: {len(merged_df)}")

        if merged_df.empty:
            logger.warning("No matching links with valid observed flow found for comparison.")
            return

        # Calculate metrics
        obs_flow = merged_df['Observed_Flow']
        est_flow = merged_df['Estimated_Flow']

        r2 = r2_score(obs_flow, est_flow)
        rmse = root_mean_squared_error(obs_flow, est_flow, squared=False) # squared=False gives RMSE

        logger.info("Total Flow Comparison Results:")
        logger.info(f"  Number of links compared: {len(merged_df)}")
        logger.info(f"  R-squared: {r2:.4f}")
        logger.info(f"  RMSE: {rmse:.4f}")

    except KeyError as e:
        logger.error(f"Missing expected column for flow comparison: {e}", exc_info=True)
    except Exception as e:
        logger.exception(f"Error during total flow comparison: {e}")

def hvlv_comparison(observed: pd.DataFrame, assigned: pd.DataFrame) -> None:
    """Compare observed HV/LV flows with assigned/decomposed HV/LV flows."""
    logger.info("Performing HV/LV flow comparison...")
    try:
        observed_renamed = observed.rename(columns={'from': 'From', 'to': 'To'}, errors='ignore')

        # Merge observed and assigned data on 'From' and 'To'
        merged_df = pd.merge(
            observed_renamed[['From', 'To', 'HV', 'LV']],
            assigned[['From', 'To', 'HV', 'LV']],
            on=['From', 'To'],
            how='inner',
            suffixes=('', '_Flow')
        )
        merged_df.drop_duplicates(subset=['From', 'To'], inplace=True)
        logger.debug(f"Merged observed and assigned HV/LV flows for {len(merged_df)} links.")

        merged_df.dropna(subset=['HV', 'LV'], inplace=True)

        logger.debug(f"Number of links after filtering NaN observed flows: {len(merged_df)}")


        if merged_df.empty:
            logger.warning("No matching links with valid observed HV/LV flow found for comparison.")
            return

        # Calculate metrics for HV
        obs_hv = merged_df['HV']
        est_hv = merged_df['HV_Flow']
        r2_hv = r2_score(obs_hv, est_hv)
        rmse_hv = root_mean_squared_error(obs_hv, est_hv, squared=False)

        # Calculate metrics for LV
        obs_lv = merged_df['LV']
        est_lv = merged_df['LV_Flow']
        r2_lv = r2_score(obs_lv, est_lv)
        rmse_lv = root_mean_squared_error(obs_lv, est_lv, squared=False)

        logger.info("HV/LV Flow Comparison Results:")
        logger.info(f"  Number of links compared: {len(merged_df)}")
        logger.info("  --- Heavy Vehicles (HV) ---")
        logger.info(f"    R-squared: {r2_hv:.4f}")
        logger.info(f"    RMSE: {rmse_hv:.4f}")
        logger.info("  --- Light Vehicles (LV) ---")
        logger.info(f"    R-squared: {r2_lv:.4f}")
        logger.info(f"    RMSE: {rmse_lv:.4f}")

    except KeyError as e:
        logger.error(f"Missing expected column for HV/LV comparison: {e}", exc_info=True)
    except Exception as e:
        logger.exception(f"Error during HV/LV flow comparison: {e}")


def evaluate(path: Path, run_subprocess: bool) -> None:
    """Evaluate the results of the GA/refine process against observed flows."""
    logger.info(f"Starting evaluation process for path: {path}")
    logger.debug(f"Run subprocess flag: {run_subprocess}")

    try:
        # Find the latest observed travel time/flow file
        ttfolder = path / 'Traveltimes'
        if not ttfolder.exists():
            logger.error(f"Traveltimes folder not found: {ttfolder}")
            raise FileNotFoundError(f"Traveltimes folder not found: {ttfolder}")

        tt_files = list(ttfolder.glob('*.csv'))
        if not tt_files:
            logger.error(f"No CSV files found in Traveltimes folder: {ttfolder}")
            raise FileNotFoundError(f"No CSV files found in {ttfolder}")

        ttfile = max(tt_files, key=os.path.getctime)
        logger.info(f"Using latest observed data file: {ttfile}")
        observed = pd.read_csv(ttfile)

        # Check if observed flow data is present
        if 'demand' not in observed.columns or observed['demand'].isnull().all():
            logger.warning("No 'demand' column or only null values found in observed data.")
            logger.warning("Evaluation requires observed flow data ('demand' column).")
            return

        try:
            results_folder_name = ttfile.stem.split('LinkTT_')[1]
        except IndexError:
            logger.error(f"Could not extract results folder name from filename: {ttfile.name}")
            logger.error("Expected filename format like '...LinkTT_xx.csv'")
            raise ValueError(f"Invalid filename format for {ttfile.name}")

        zone_folders = list(path.glob('Zones_*'))
        if not zone_folders:
            logger.error(f"No 'Zones_*' folder found in {path}")
            raise FileNotFoundError(f"No 'Zones_*' folder found in {path}")
        zone_folder = max(zone_folders, key=os.path.getctime)
        logger.debug(f"Using zone folder: {zone_folder}")

        results_base = zone_folder / 'Results' / results_folder_name
        estimate_folder = results_base / 'Estimation'
        assig_folder = results_base / 'Assignments'

        if not estimate_folder.exists():
            logger.error(f"Estimation folder not found: {estimate_folder}")
            raise FileNotFoundError(f"Estimation folder not found: {estimate_folder}")
        if not assig_folder.exists():
            logger.warning(f"Assignments folder not found: {assig_folder}. Will attempt to create if subprocess runs.")

        est_cap_folders = list(estimate_folder.glob('Cap*'))
        if not est_cap_folders:
            logger.error(f"No 'Cap*' subfolder found in estimation folder: {estimate_folder}")
            raise FileNotFoundError(f"No 'Cap*' subfolder found in {estimate_folder}")
        final_estimate_folder = max(est_cap_folders, key=os.path.getctime)
        logger.debug(f"Using final estimation folder: {final_estimate_folder}")

        # Check if observed data has HV/LV split
        hvlv_present = ('HV' in observed.columns and not observed['HV'].isnull().all() and
                        'LV' in observed.columns and not observed['LV'].isnull().all())
        logger.debug(f"Observed data has HV/LV split: {hvlv_present}")

        # --- Main Evaluation Logic ---
        try:
            # Check if OD results file exists (implies refine was run)
            estimated_od_file = final_estimate_folder / \
                'Elite0ODResults.csv'  # Changed from _best
            if not estimated_od_file.exists():
                logger.warning(
                    f"OD results file not found: {estimated_od_file}. Falling back to LinkResults.")
                raise FileNotFoundError  # Trigger fallback to LinkResults evaluation

            logger.info(
                f"Found OD results file: {estimated_od_file}. Proceeding with evaluation.")

            # Determine if subprocesses are needed
            run_assign_subprocess = False
            run_decompose_subprocess = False

            if run_subprocess:
                # Find the latest assignment results folder *before* deciding to run subprocess
                latest_assig_cap_folder = None
                if assig_folder.exists():
                    assig_cap_folders = list(assig_folder.glob('Cap*'))
                    if assig_cap_folders:
                        latest_assig_cap_folder = max(
                            assig_cap_folders, key=os.path.getctime)

                if latest_assig_cap_folder is None:
                    logger.info(
                        f"Assignments folder/Cap* subfolder not found ({assig_folder}). Will run 'assign' subprocess.")
                    run_assign_subprocess = True
                    if hvlv_present:
                        logger.info(
                            "Will also run 'decompose' subprocess as assignment folder is missing.")
                        run_decompose_subprocess = True
                elif hvlv_present:
                    hvlv_split_file = latest_assig_cap_folder / 'HV_LV_Link_splits.csv'
                    if not hvlv_split_file.exists():
                        logger.info(
                            f"HV/LV split file not found ({hvlv_split_file}). Will run 'decompose' subprocess.")
                        run_decompose_subprocess = True
                    else:
                        logger.info(
                            f"Required assignment results and HV/LV split file found ({hvlv_split_file}). No subprocess needed.")
                else:
                    logger.info(
                        f"Assignment folder found ({latest_assig_cap_folder}) and HV/LV not present. No subprocess needed.")

            # Run subprocesses if needed and enabled
            if run_assign_subprocess or run_decompose_subprocess:
                logger.info("Running required subprocesses...")
                try:
                    if run_assign_subprocess:
                        cmd_assign = ['rapidxpy', 'plan', 'assign',
                                    '-odp', '-od', str(estimated_od_file)]
                        logger.debug(
                            f"Running command: {' '.join(cmd_assign)} in {path}")
                        subprocess.run(
                            cmd_assign, cwd=path, check=True, capture_output=True, text=True)
                        logger.info("Assignment subprocess completed.")
                        # After running assign, the assignments folder should exist
                        if not assig_folder.exists():  # Re-check just in case
                            # Create if needed
                            assig_folder.mkdir(parents=True, exist_ok=True)

                    if run_decompose_subprocess:  # hvlv_present is implied if this is True
                        cmd_decompose = [
                            'rapidxpy', 'plan', 'decompose', '-od', str(estimated_od_file)]
                        logger.debug(
                            f"Running command: {' '.join(cmd_decompose)} in {path}")
                        subprocess.run(cmd_decompose, cwd=path,
                                    check=True, capture_output=True, text=True)
                        logger.info("Decomposition subprocess completed.")

                except subprocess.CalledProcessError as e:
                    logger.error(f"Subprocess failed: {e}")
                    logger.error(f"Command: {e.cmd}")
                    logger.error(f"Stderr: {e.stderr}")
                    logger.error(f"Stdout: {e.stdout}")
                    raise Exception(
                        f"Subprocess execution failed: {e.cmd}") from e
                except FileNotFoundError:
                    logger.error(
                        "`rapidxpy` command not found. Ensure RapidxPy is installed and in the system PATH.")
                    raise

            # --- Proceed with comparison using potentially newly generated files ---

            # Find the latest assignment results folder again
            if not assig_folder.exists():
                # This should not happen if assign subprocess ran successfully, but check anyway
                logger.error(
                    f"Assignments folder still not found after potential subprocess run: {assig_folder}")
                raise FileNotFoundError(
                    f"Assignments folder not found: {assig_folder}")

            assig_cap_folders = list(assig_folder.glob('Cap*'))
            if not assig_cap_folders:
                logger.error(
                    f"No 'Cap*' subfolder found in assignments folder: {assig_folder}")
                raise FileNotFoundError(
                    f"No 'Cap*' subfolder found in {assig_folder}")
            final_assig_folder = max(assig_cap_folders, key=os.path.getctime)
            logger.debug(
                f"Using final assignment folder: {final_assig_folder}")

            # Try to load HV/LV split results if relevant
            if hvlv_present:
                hvlv_split_file = final_assig_folder / 'HV_LV_Link_splits.csv'
                if hvlv_split_file.exists():
                    logger.info(
                        f"Found HV/LV split file: {hvlv_split_file}. Performing HV/LV comparison.")
                    assigned = pd.read_csv(hvlv_split_file)
                    hvlv_comparison(observed, assigned)
                else:
                    # HV/LV split file not found, even after potential decompose run
                    msg = f"HV/LV split file not found: {hvlv_split_file}"
                    logger.error(
                        msg + ". Decomposition might have failed or did not produce the expected file.")
                    # Fallback to total flow comparison? Or raise error? Raising for clarity.
                    raise Exception(msg + '. Error in decompose?')
            else:
                # HV/LV not present, perform total flow comparison using assignment linkTT
                logger.info(
                    "HV/LV not present in observed data. Performing total flow comparison using assignment results.")
                linktt_file = final_assig_folder / 'linkTT.csv'
                if linktt_file.exists():
                    logger.info(
                        f"Using assignment linkTT file for total flow comparison: {linktt_file}")
                    estimated_link_results = pd.read_csv(linktt_file)
                    # Rename columns to match flow_comparison expectations if necessary
                    estimated_link_results.rename(columns={
                                                'EstimatedTT': 'Estimated_TT', 'Volume': 'Estimated_Flow'}, inplace=True, errors='ignore')
                    flow_comparison(observed, estimated_link_results)
                else:
                    logger.error(
                        f"Assignment linkTT file also not found: {linktt_file}. Cannot perform flow comparison.")
                    raise FileNotFoundError(
                        f"Required assignment result file not found: {linktt_file}")

        except FileNotFoundError:
            # Fallback: OD results not found, use LinkResults from estimation
            logger.warning(
                "OD results file not found. Evaluating using estimation LinkResults.")
            estimated_link_file = final_estimate_folder / 'Elite0LinkResults.csv'
            if not estimated_link_file.exists():
                logger.error(
                    f"Estimation LinkResults file not found: {estimated_link_file}")
                raise FileNotFoundError(
                    f"Required estimation result file not found: {estimated_link_file}")

            logger.info(
                f"Using estimation LinkResults file: {estimated_link_file}")
            estimated_link_results = pd.read_csv(estimated_link_file)

            if hvlv_present:
                logger.warning(
                    "Observed data has HV/LV split, but evaluation is based on total flow from estimation LinkResults as OD results were missing.")
                # Optionally, still check if decompose was run and produced results
                try:
                    assig_cap_folders = list(assig_folder.glob('Cap*'))
                    if assig_cap_folders:
                        final_assig_folder = max(
                            assig_cap_folders, key=os.path.getctime)
                        hvlv_split_file = final_assig_folder / 'HV_LV_Link_splits.csv'
                        if hvlv_split_file.exists():
                            logger.info(
                                f"Found HV/LV split file in assignments folder: {hvlv_split_file}. Performing HV/LV comparison instead of total flow.")
                            assigned = pd.read_csv(hvlv_split_file)
                            hvlv_comparison(observed, assigned)
                            return  # Exit after successful HV/LV comparison
                        else:
                            logger.warning(
                                f"HV/LV split file not found in {final_assig_folder}. Proceeding with total flow comparison using estimation results.")
                    else:
                        logger.warning(
                            "No 'Cap*' assignment folder found. Cannot check for HV/LV split results. Proceeding with total flow comparison using estimation results.")
                except Exception as e:
                    logger.warning(
                        f"Error checking for assignment HV/LV results during fallback: {e}. Proceeding with total flow comparison using estimation results.")

                # If HV/LV comparison didn't happen above, do total flow comparison
                logger.info(
                    "Performing total flow comparison using estimation LinkResults.")
                # Rename columns to match flow_comparison expectations
                estimated_link_results.rename(columns={
                                            'EstimatedTT': 'Estimated_TT', 'Volume': 'Estimated_Flow'}, inplace=True, errors='ignore')
                flow_comparison(observed, estimated_link_results)

            else:
                # No HV/LV in observed data, just do total flow comparison
                logger.info(
                    "Performing total flow comparison using estimation LinkResults.")
                # Rename columns to match flow_comparison expectations
                estimated_link_results.rename(columns={
                                            'EstimatedTT': 'Estimated_TT', 'Volume': 'Estimated_Flow'}, inplace=True, errors='ignore')
                flow_comparison(observed, estimated_link_results)

    except FileNotFoundError as e:
        logger.error(
            f"Evaluation failed: Required file or folder not found. {e}", exc_info=True)
        raise
    except Exception as e:
        logger.exception(
            f"An unexpected error occurred during evaluation: {e}")
        raise

    logger.info("Evaluation process completed.")

def get_local_timezone(zone: str) -> str:
    """Get the local timezone for a given zone file."""
    temp_gdf = gpd.read_file(zone)
    centroid = temp_gdf.geometry.centroid.iloc[0]
    lon, lat = centroid.x, centroid.y
    tf = TimezoneFinder()
    tz_str = tf.timezone_at(lat=lat, lng=lon)
    return tz_str
