#!/usr/bin/env python3

"""
Partitions test classes across shards for parallel CI execution.

Mode 1 - Generate all shard filters to files:
    dotnet test --list-tests ... | python3 partition_tests.py generate <total-shards> <output-dir> [timings-file]
    Writes <output-dir>/shard_0.runsettings .. shard_N.runsettings


Mode 2 - Read a pre-generated filter file:
    python3 partition_tests.py read <runsettings-file>
    Prints the filter to stdout (empty output if file is empty/missing)

Mode 3 - Harvest measured timings from CI test results:
    python3 partition_tests.py harvest <trx-dir> <output-json>
    Walks <trx-dir> for *.trx files, sums the real execution duration per test
    method, and writes {method: seconds} to <output-json>.

Exit codes:
    0 - success
    1 - error (bad arguments or no tests discovered in generate mode)
"""

import sys
import os
import json
import glob
import math
import bisect
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime

DEFAULT_TIMINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-timings.json")

# Each shard's runsettings is built from the same base the unsharded jobs use
# (.github/ci.runsettings), with only the shard's <Where> filter added, so the
# integration and content jobs share one NUnit run config. The script lives at
# Tools/_Starlight/, so the repo root is two directories up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BASE_RUNSETTINGS = os.path.join(_REPO_ROOT, ".github", "ci.runsettings")


# test for now.
HARVEST_SIG_FIGS = 2        # round timing to x numbers
HARVEST_REL_DELTA = 0.15    # only updated if >=x% of previous result
HARVEST_ABS_DELTA = 0.05    # only update if test timing changes by >=x seconds

# Looking at this, you are probably thinking,
# Monsieur, have you lost your mind.
# But this is a permanent solution. The multithreading in the engine for tests will not be fixed anytime soon, all of this is here forever.
# See https://github.com/dotnet/runtime/issues/107197, who knows, maybe by time time you see this it will be fixed.
# How do you use it? Run the test, take the one that finished the fastest and decrease its weight, then increase the weight of the slowest one until they balance out.
WEIGHT_OVERRIDES = {
    "AbsorbentOnRefillableTest": 0.125,
    "AbsorbentOnSmallRefillableTest": 0.125,
    "AddListRemoveObjectiveTest": 0.125,
    "AddPlayerSessionLog": 0.25,
    "AdjustJobsTest": 0.5,
    "AgeRequirementsTest": 0.5,
    "AirConsistencyTest": 0.5,
    "AirlockBlockTest": 0.5,
    "AllCommandsHaveDescriptions": 0.5,
    "AllComponentsOneToOneDeleteTest": 0.5,
    "AllItemsHaveSpritesTest": 0.25,
    "AllMapsTested": 0.5,
    "AllSalvageMapsLoadableTest": 5.0,
    "AndTest": 0.5,
    "ApcChargingTest": 0.5,
    "ApcNetTest": 1.0,
    "ArmBladeActivateDeactivateTest": 0.5,
    "AutoRecordReplayTest": 0.25,
    "BananaSlipTest": 0.5,
    "BucklePullTest": 0.25,
    "BuckleInteractBuckleUnbuckleSelf": 0.5,
    "BuckleUnbuckleCooldownRangeTest": 0.25,
    "BulkAddLogs": 0.25,
    "CancelRepeatedWeld": 0.25,
    "CancelTilePry": 0.5,
    "CancelWallConstruct": 0.5,
    "ChairTest": 0.25,
    "ChasmFallTest": 0.5,
    "ChasmGrappleTest": 0.25,
    "ClientPrototypeSaveLoadSaveTest": 0.125,
    "CommsServerKeys": 0.25,
    "Component_InitDataCorrect": 0.25,
    "ConstructProtolathe": 0.25,
    "ConstructReinforcedWindow": 0.5,
    "ConstructionGraphEdgeValid": 0.25,
    "ConstructionGraphSpawnPrototypeValid": 0.5,
    "CraftGrenade": 0.25,
    "CraftRods": 0.5,
    "CreateDeleteCreateTest": 0.25,
    "CreateSaveLoadSaveGrid": 0.25,
    "Date": 0.0625,
    "DeconstructComputer": 0.25,
    "DeconstructTable": 0.0625,
    "DeconstructWall": 0.25,
    "DeconstructWindow": 0.5,
    "Delete_CacheUpdatesOnAtmosTick": 0.25,
    "DeonstructReinforcedWindow": 0.25,
    "DeserializeNullDefinitionTest": 0.5,
    "DeserializeNullTest": 0.5,
    "DisciplineValidTierPrerequesitesTest": 0.5,
    "DispenseItemTest": 0.125,
    "DragDropOntoDrainTest": 0.125,
    "DragDropOpensStrip": 0.5,
    "DuplicatePlayerIdDoesNotThrowTest": 0.5,
    "EORPluralizationTest": 0.5,
    "EmergencyEvacTest": 0.5,
    "EnsureNoEdgeClobbering": 0.5,
    "EntityEntityTest": 1.0,
    "EntityShowDepartmentsAndJobs": 0.25,
    "FillLevelSpritesExist": 0.0625,
    "FireSpreading": 0.25,
    "FloorConstructDeconstruct": 0.25,
    "FollowerMapDeleteTest": 0.125,
    "ForceUnbuckleBuckleTest": 0.5,
    "GasSpecificHeats_Agree": 0.5,
    "GasSpreading": 0.5,
    "GetAndReturnCup": 0.25,
    "HeadsetKeys": 0.25,
    "HeatScaleCVar_Replicates_Agree": 0.25,
    "HumanMoveOverTest": 0.125,
    "HungerThirstIncreaseDecreaseTest": 3.0,
    "IgnoredComponentsExistInTheCorrectPlaces": 0.5,
    "InsertAndDispenseItemTest": 0.125,
    "InsertDumpableInsertableItemTest": 0.5,
    "InsertEjectBuiTest": 0.0625,
    "InsideContainerInteractionBlockTest": 0.25,
    "InteractUITest": 0.25,
    "InteractionOutOfRangeTest": 0.5,
    "InteractionTest": 0.25,
    "JobPreferenceTest": 0.25,
    "JobWeightTest": 1.0,
    "KillAndReviveTest": 0.5,
    "LoadSaveTicksSave": 0.5,
    "LoadTickLoad": 0.5,
    "MagazineVisualsSpritesExist": 0.125,
    "MicrowaveRecipesFreezeTest": 0.125,
    "MouseMoveOverTest": 0.25,
    "MultiTile_Component_InitDataCorrect": 0.25,
    "MultiTile_Delete_CacheUpdatesOnAtmosTick": 0.25,
    "MultiTile_Spawn_CacheUpdatesOnAtmosTick": 0.125,
    "NoCargoBountyArbitrageTest": 0.25,
    "NoCargoOrderArbitrage": 0.25,
    "NoMaterialArbitrage": 15.0,
    "NoSavedPostMapInitTest": 30.0,
    "NoSliceableBountyArbitrageTest": 0.5,
    "NonGameMapsLoadableTest": 80.0,
    "NullOutTileAtmosphereGasMixture": 0.5,
    "PardonTest": 0.25,
    "ParseTestDocument": 2.0,
    "PlaceThenCutLattice": 2.0,
    "PoweredClosedAirlock_Pry_DoesNotOpen": 0.25,
    "PoweredOpenAirlock_Pry_DoesNotClose": 0.25,
    "PreRoundAddAndGetSingle": 0.5,
    "ProcessingAbsoluteDamageTest": 0.25,
    "ProcessingAbsoluteStandbyTest": 0.25,
    "ProcessingDeltaDamageTest": 0.125,
    "ProcessingListAutoJoinTest": 0.5,
    "PrototypesHaveKnownComponents": 2.0,
    "PryLattice": 0.25,
    "PullerIsConsideredInteractingTest": 2.0,
    "PullerSanityTest": 0.5,
    "QuerySingleLog": 0.5,
    "RejuvenateDeadTest": 0.25,
    "Relogin": 0.5,
    "RepairReinforcedWindow": 0.5,
    "ResettingEntitySystemResetTest": 0.25,
    "RestartRoundAfterStart": 0.5,
    "RestartTest": 0.5,
    "RestockTest": 0.5,
    "SelectionTest": 0.5,
    "ServerPrototypeSaveLoadSaveTest": 30.0,
    "SetWorkingState_AlreadyInState_NoChange": 0.5,
    "SetWorkingState_IdleToWorking_UpdatesLoad": 0.25,
    "ShuttlesLoadableTest": 70.0,
    "SpaceNoPuddleTest": 0.25,
    "SpawnAndDeleteAllEntitiesInTheSameSpot": 60.0,
    "SpawnAndDeleteAllEntitiesOnDifferentMaps": 100.0,
    "SpawnAndDeleteEntityCountTest": 115.0,
    "SpawnAndDirtyAllEntities": 240.0,
    "SpawnItemInSlotTest": 0.25,
    "Spawn_CacheUpdatesOnAtmosTick": 0.125,
    "Spawn_ReconstructedUpdatesImmediately": 0.5,
    "SpillCorner": 0.5,
    "StackPrice": 0.5,
    "StartRoundTest": 0.5,
    "StopHardCodingWidgetsJesusChristTest": 2.0,
    "StorageSizeArbitrageTest": 0.25,
    "TakeRoleAndReturn": 0.125,
    "TestAb": 0.5,
    "TestAddRemoveHasRoles": 2.0,
    "TestAlarmThreshold": 0.5,
    "TestAllClientPrototypesAreSerializable": 35.0,
    "TestAllConcurrent": 0.25,
    "TestAllRestocksAreAvailableToBuy": 0.5,
    "TestAllServerPrototypesAreSerializable": 35.0,
    "TestApcLoad": 10.0,
    "TestBatteriesProportional": 0.5,
    "TestBatteryRamp": 0.25,
    "TestBladeServerBoardHasValidBladeServer": 0.25,
    "TestClientStart": 0.25,
    "TestCombatActionsAdded": 0.5,
    "TestComputerBoardHasValidComputer": 0.25,
    "TestConnect": 0.5,
    "TestDamageSpecifierOperations": 0.5,
    "TestDeleteCharacter": 0.5,
    "TestDeleteThrownItem": 0.5,
    "TestDeleteVisiting": 0.5,
    "TestDeletedCanReconnect": 0.25,
    "TestDisconnectWhileEmbedded": 0.5,
    "TestDockingConfig": 0.5,
    "TestDungeonPresets": 0.25,
    "TestDungeonRoomPackBounds": 0.25,
    "TestDuplicatePrevention": 0.25,
    "TestEntityDeadWhenGibbed": 0.0625,
    "TestFinished": 0.25,
    "TestFullBattery": 0.0625,
    "TestGasArrayDeserialization": 0.5,
    "TestGhostDoesNotInfiniteLoop": 0.5,
    "TestGhostGridNotTerminating": 0.5,
    "TestGhostsCanReconnect": 1.0,
    "TestGib": 0.25,
    "TestGridGhostOnQueueDelete": 0.5,
    "TestGridJoinAtmosphere": 0.125,
    "TestInternalsAutoActivateInSpaceForEntitySpawn": 0.5,
    "TestLatheRecipeIngredientsFitLathe": 0.5,
    "TestLayoutInheritance": 0.25,
    "TestLobbyPlayersValid": 0.25,
    "TestLogErrorCausesTestFailure": 0.5,
    "TestMindTransfersToOtherEntity": 0.5,
    "TestNoDemandRampdown": 0.5,
    "TestNoManualEntityLocStrings": 0.5,
    "TestOriginalDeletedWhileGhostingKeepsGhost": 0.25,
    "TestOwningPlayerCanBeChanged": 0.25,
    "TestPickupDrop": 0.5,
    "TestPlayerCanGhost": 0.5,
    "TestPvsCommands": 2.0,
    "TestReplaceMind": 0.5,
    "TestRestockBreaksOpen": 0.5,
    "TestRestockInventoryBounds": 2.0,
    "TestSerializable": 0.25,
    "TestSimpleBatteryChargeDeficit": 0.25,
    "TestSimpleDeficit": 0.5,
    "TestStartIsValid": 0.25,
    "TestStartReachesValidTarget": 0.125,
    "TestStartingGearStorage": 0.5,
    "TestStaticAnchorPrototypes": 0.25,
    "TestStationStartingPowerWindow": 0.125,
    "TestStorageFillPrototypes": 0.25,
    "TestSufficientSpaceForEntityStorageFill": 0.0625,
    "TestSufficientSpaceForFill": 0.5,
    "TestSuicide": 0.5,
    "TestSuicideByHeldItemSpreadDamage": 0.5,
    "TestSuicideWhileDamaged": 0.5,
    "TestSupplyPrioritized": 0.5,
    "TestSupplyRamp": 0.125,
    "TestTags": 0.5,
    "TestTargetIsValid": 0.5,
    "TestTemperatureCalculations": 0.25,
    "TestTerminalNodeGroups": 0.25,
    "TestThrownEggBreaks": 2.0,
    "TestUserDoesNotExist": 2.0,
    "TestVisitingReconnect": 0.5,
    "ThrowItemIntoDisposalUnitTest": 0.125,
    "TryAddTooMuchNonReactiveReagent": 0.25,
    "TryAddTwoNonReactiveReagent": 0.25,
    "TryAllTest": 0.5,
    "TryMixAndOverflowTooMuchReagent": 0.5,
    "TryStopNukeOpsFromConstantlyFailing": 0.125,
    "UiInteractTest": 2.0,
    "UnpoweredOpenAirlock_Pry_Closes": 0.5,
    "ValidateJobPrototypes": 0.125,
    "ValidateMobThresholds": 0.125,
    "ValidatePrototypeContents": 0.5,
    "ValidateRolePrototypes": 65.0,
    "WeightlessStatusTest": 0.25,
    "WindowOnGrille": 0.25,
    "WirelessNetworkDeviceSendAndReceive": 0.25,
    "WiresPanelScrewing": 0.25,
    "XenoArtifactBuildActiveNodesTest": 0.25,
    "XenoArtifactRemoveNodeTest": 0.5,
    "XenoArtifactResizeTest": 1.0,
}


def parse_tests(lines):
    """Parse test names from `dotnet test --list-tests` output."""
    tests = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if "The following Tests are available:" in stripped:
            in_list = True
            continue
        if in_list and stripped:
            tests.append(stripped)
    return tests


def extract_classes(tests):
    """Extract unique test method groups with test counts from display names.

    --list-tests outputs display names:
      - Windows:  MethodName  or  MethodName(params)
      - Linux:    FixtureName.MethodName  or  FixtureName.MethodName(params)

    We always extract the METHOD name as the group key so behaviour is
    consistent across platforms and the Name~ filter works everywhere.
    """
    counts = {}
    for test in tests:
        name = test.split("(")[0].strip()
        # If format is "Fixture.Method", take just the method part
        dot = name.rfind(".")
        method = name[dot + 1:] if dot > 0 else name
        counts[method] = counts.get(method, 0) + 1
    return counts


def load_timings(path):
    """Load {method: seconds} measured timings, or None if unavailable.

    A missing or unparseable file is not fatal: the generator falls back to
    the legacy count * WEIGHT_OVERRIDES weighting so CI never breaks just
    because timings haven't been harvested yet.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Warning: could not read timings file {path}: {e}", file=sys.stderr)
        return None
    # Coerce to floats and drop anything non-positive/garbage.
    timings = {}
    for method, seconds in data.items():
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            continue
        if s > 0:
            timings[method] = s
    return timings or None


def parse_duration(text):
    """Parse a TRX duration string 'HH:MM:SS.fffffff' into seconds."""
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        hms, _, frac = text.partition(".")
        h, m, s = (int(p) for p in hms.split(":"))
        seconds = h * 3600 + m * 60 + s
        if frac:
            seconds += int(frac) / (10 ** len(frac))
        return float(seconds)
    except (ValueError, AttributeError):
        return 0.0


def round_sig(x, sig):
    if x <= 0:
        return 0.0
    digits = sig - 1 - math.floor(math.log10(x))
    return round(x, digits)


def stabilise_timings(new_timings, old_timings):
    result = {}
    kept = updated = added = 0
    for method in sorted(new_timings):
        new = round_sig(new_timings[method], HARVEST_SIG_FIGS)
        old = old_timings.get(method) if old_timings else None
        if old is None:
            result[method] = new
            added += 1
            continue
        delta = abs(new - old)
        if delta >= HARVEST_ABS_DELTA and delta >= HARVEST_REL_DELTA * old:
            result[method] = new
            updated += 1
        else:
            result[method] = old
            kept += 1
    return result, (kept, updated, added)


def method_of(test_name):
    """Reduce a TRX/list-tests display name to its bare method name."""
    name = test_name.split("(")[0].strip()
    dot = name.rfind(".")
    return name[dot + 1:] if dot > 0 else name


def build_filter(methods):
    """Build a NUnit.Where expression from method names.

    Uses NUnit Test Selection Language with exact method name matching.
    This avoids substring issues (e.g. 'Test' matching 'TestConnect')
    that plague VSTest Name~ filters.
    """
    if not methods:
        return ""
    return "||".join(f"method=='{m}'" for m in sorted(methods))


def load_base_runsettings(path):
    """Read the base runsettings text, or None if it cannot be read."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        print(f"Warning: could not read base runsettings {path}: {e}", file=sys.stderr)
        return None


def build_runsettings(base_text, filter_expr):
    """Return the base runsettings with the shard's NUnit <Where> filter added.

    The base is .github/ci.runsettings, so every shard inherits the same NUnit
    run config (e.g. MapWarningTo, and anything added later) as the unsharded
    jobs. The <Where> is injected just before the closing </NUnit>. If the base
    is missing or has no <NUnit> section, fall back to a minimal wrapper so
    generation never fails.
    """
    where = f"<Where>{filter_expr}</Where>"
    if base_text:
        m = re.search(r"([ \t]*)</NUnit>", base_text)
        if m:
            indent = m.group(1) + "  "
            return f"{base_text[:m.start()]}{indent}{where}\n{base_text[m.start():]}"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            "<RunSettings>\n"
            "  <NUnit>\n"
            "    <MapWarningTo>Failed</MapWarningTo>\n"
            f"    {where}\n"
            "  </NUnit>\n"
            "</RunSettings>\n")


def cmd_generate():
    if len(sys.argv) not in (4, 5):
        print(f"Usage: {sys.argv[0]} generate <total-shards> <output-dir> [timings-file]", file=sys.stderr)
        sys.exit(1)

    total = int(sys.argv[2])
    output_dir = sys.argv[3]
    timings_path = sys.argv[4] if len(sys.argv) == 5 else DEFAULT_TIMINGS_FILE

    lines = sys.stdin.read().splitlines()
    tests = parse_tests(lines)

    if not tests:
        print("Error: no tests discovered from input", file=sys.stderr)
        sys.exit(1)

    class_counts = extract_classes(tests)
    print(f"Discovered {len(tests)} tests in {len(class_counts)} classes, distributing across {total} shards", file=sys.stderr)

    timings = load_timings(timings_path)

    if timings:
        # Estimate unknown methods from the median per-test duration.
        rates = sorted(timings[c] / class_counts[c] for c in class_counts if c in timings)
        median_per_test = statistics.median(rates) if rates else 1.0
        print(f"Using measured timings from {timings_path}: "
              f"{len(rates)}/{len(class_counts)} classes have data, "
              f"fallback = {median_per_test:.3f}s/test (median)", file=sys.stderr)

        def class_weight(cls):
            if cls in timings:
                return timings[cls]
            return class_counts[cls] * median_per_test
    else:
        # no timings file, weight by count * manual override.
        print(f"No timings file at {timings_path}; using WEIGHT_OVERRIDES fallback", file=sys.stderr)

        def class_weight(cls):
            multiplier = WEIGHT_OVERRIDES.get(cls, 1.0)
            return class_counts[cls] * multiplier

    os.makedirs(output_dir, exist_ok=True)

    base_runsettings = load_base_runsettings(DEFAULT_BASE_RUNSETTINGS)
    if base_runsettings is None:
        print(f"No base runsettings at {DEFAULT_BASE_RUNSETTINGS}; using minimal template", file=sys.stderr)
    else:
        print(f"Extending base runsettings {DEFAULT_BASE_RUNSETTINGS} with each shard's filter", file=sys.stderr)

    # Greedy load-balancing: assign heaviest classes first to least-loaded shard
    shards = [[] for _ in range(total)]
    shard_loads = [0.0] * total
    for cls in sorted(class_counts, key=class_weight, reverse=True):
        lightest = min(range(total), key=lambda s: shard_loads[s])
        shards[lightest].append(cls)
        shard_loads[lightest] += class_weight(cls)

    for shard in range(total):
        my_classes = sorted(shards[shard])
        filter_expr = build_filter(my_classes)
        print(f"  Shard {shard}: {len(my_classes)} classes, weight {shard_loads[shard]:.1f} ({sum(class_counts[c] for c in my_classes)} tests)", file=sys.stderr)
        for cls in my_classes:
            w = class_weight(cls)
            print(f"    - {cls} ({class_counts[cls]} tests, weight {w:.1f})", file=sys.stderr)

        rs_path = os.path.join(output_dir, f"shard_{shard}.runsettings")
        with open(rs_path, "w", newline="\n") as f:
            f.write(build_runsettings(base_runsettings, filter_expr))

def cmd_read():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} read <runsettings-file>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[2]
    if not os.path.exists(path):
        return
    with open(path) as f:
        content = f.read().strip()

    # Parse the XML content
    root = ET.fromstring(content)
    where = root.findtext("NUnit/Where", default="").strip()
    if where:
        methods = [part.replace("method==", "").strip("' ") for part in where.split("||")]
        print(f"Running {len(methods)} test groups:", file=sys.stderr)
        for m in methods:
            print(f"  - {m}", file=sys.stderr)
        print(where)


def cmd_harvest():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} harvest <trx-dir> <output-json>", file=sys.stderr)
        sys.exit(1)

    trx_dir = sys.argv[2]
    output_json = sys.argv[3]

    trx_files = glob.glob(os.path.join(trx_dir, "**", "*.trx"), recursive=True)
    if not trx_files:
        print(f"Error: no .trx files found under {trx_dir}", file=sys.stderr)
        sys.exit(1)

    totals = {}
    counts = {}
    parsed = 0
    for path in trx_files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as e:
            print(f"Warning: skipping unparseable {path}: {e}", file=sys.stderr)
            continue
        parsed += 1
        for el in root.iter():
            if not el.tag.endswith("UnitTestResult"):
                continue
            name = el.get("testName")
            if not name:
                continue
            method = method_of(name)
            totals[method] = totals.get(method, 0.0) + parse_duration(el.get("duration"))
            counts[method] = counts.get(method, 0) + 1

    if not totals:
        print(f"Error: parsed {parsed} TRX files but found no test results", file=sys.stderr)
        sys.exit(1)

    old_timings = load_timings(output_json)
    result, (kept, updated, added) = stabilise_timings(totals, old_timings)

    with open(output_json, "w", newline="\n") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")

    total_seconds = sum(result.values())
    print(f"Harvested {len(result)} methods ({sum(counts.values())} results) "
          f"from {parsed} TRX files, {total_seconds:.1f}s total -> {output_json}", file=sys.stderr)
    print(f"  Stabilised vs previous: {updated} updated, {kept} unchanged, "
          f"{added} new (dead-band {HARVEST_REL_DELTA:.0%}/{HARVEST_ABS_DELTA}s, "
          f"{HARVEST_SIG_FIGS} sig figs)", file=sys.stderr)


def _parse_trx_epoch(text):
    """Parse a TRX startTime/endTime
    """
    text = (text or "").strip()
    if not text:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", text)
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = ("." + frac[1:7]) if frac else ""
    tz = "" if tz is None else ("+00:00" if tz == "Z" else tz)
    try:
        dt = datetime.fromisoformat(f"{base}{frac}{tz}")
    except ValueError:
        return None
    return dt.timestamp()


def cmd_memprofile():
    """Attribute peak process memory to each test method from a sequential run.
    """
    args = sys.argv[2:]
    json_path = None
    if "--json" in args:
        i = args.index("--json")
        json_path = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        print(f"Usage: {sys.argv[0]} memprofile <trx-dir> <rss-log> [top-n] [--json <path>]", file=sys.stderr)
        sys.exit(1)
    trx_dir, rss_log = args[0], args[1]
    top_n = int(args[2]) if len(args) > 2 else 15

    # Load 'epoch used_kib' samples.
    samples = []
    try:
        with open(rss_log) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        samples.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        continue
    except OSError as e:
        print(f"Error: could not read rss log {rss_log}: {e}", file=sys.stderr)
        sys.exit(1)
    samples.sort()
    if not samples:
        print("Error: no memory samples found; cannot profile", file=sys.stderr)
        sys.exit(1)
    times = [t for t, _ in samples]
    vals = [v for _, v in samples]

    def peak_between(a, b):
        lo = bisect.bisect_left(times, a)
        hi = bisect.bisect_right(times, b)
        window = vals[lo:hi]
        return max(window) if window else None

    def value_at(t):
        i = bisect.bisect_right(times, t) - 1
        return vals[i] if i >= 0 else vals[0]

    trx_files = glob.glob(os.path.join(trx_dir, "**", "*.trx"), recursive=True)
    peak_by_method = {}
    own_by_method = {}
    missing_times = 0
    for path in trx_files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for el in root.iter():
            if not el.tag.endswith("UnitTestResult"):
                continue
            name = el.get("testName")
            if not name:
                continue
            start = _parse_trx_epoch(el.get("startTime"))
            end = _parse_trx_epoch(el.get("endTime"))
            if start is None or end is None:
                missing_times += 1
                continue
            pk = peak_between(start, end)
            if pk is None:
                continue  # test ran entirely between two samples; too fast to matter
            method = method_of(name)
            peak_by_method[method] = max(peak_by_method.get(method, 0.0), pk)
            own = pk - value_at(start)
            own_by_method[method] = max(own_by_method.get(method, 0.0), own)

    if not peak_by_method:
        if missing_times:
            print("Error: TRX files lack startTime/endTime; cannot attribute memory", file=sys.stderr)
        else:
            print("Error: no test windows overlapped the memory samples", file=sys.stderr)
        sys.exit(1)

    ranked = sorted(own_by_method.items(), key=lambda kv: -kv[1])
    if missing_times:
        print(f"note: {missing_times} test case(s) had no start/end time and were skipped.",
              file=sys.stderr)
    print(f"Profiled {len(peak_by_method)} methods from {len(trx_files)} TRX files "
          f"({len(samples)} samples).", file=sys.stderr)

    # Human/summary-friendly table on stdout so a workflow can capture it directly.
    print(f"{'own':>8}  {'peak':>8}  method")
    for method, own in ranked[:top_n]:
        peak = peak_by_method.get(method, 0.0)
        print(f"{own / 1048576:7.2f}G  {peak / 1048576:7.2f}G  {method}")

    if json_path:
        out = {m: round(own / 1024, 1) for m, own in ranked}
        with open(json_path, "w", newline="\n") as f:
            json.dump(out, f, indent=2, sort_keys=True)
            f.write("\n")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <generate|read|harvest|memprofile> ...", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "generate":
        cmd_generate()
    elif cmd == "read":
        cmd_read()
    elif cmd == "harvest":
        cmd_harvest()
    elif cmd == "memprofile":
        cmd_memprofile()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
