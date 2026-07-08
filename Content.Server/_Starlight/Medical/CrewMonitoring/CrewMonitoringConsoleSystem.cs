using System.Collections.Generic;
using System.Linq;
using Content.Shared.Implants;
using Content.Shared.Access.Systems;
using Content.Shared.Medical.SuitSensor;
using Robust.Shared.Prototypes;
using Content.Server._Starlight.Medical.CrewMonitoring;
using Content.Shared._Starlight.Implants.Components;

// ReSharper disable once CheckNamespace
namespace Content.Server.Medical.CrewMonitoring;

public sealed partial class CrewMonitoringConsoleSystem
{
    [Dependency] private SharedIdCardSystem _idCard = default!;
    [Dependency] private IPrototypeManager _proto = default!;

    private void InitializeIdentityTracking()
    {
        SubscribeLocalEvent<StoredImplantIdentityComponent, AddImplantAttemptEvent>(OnBeforeIdentityImplanted);
        SubscribeLocalEvent<StoredImplantIdentityComponent, ImplantImplantedEvent>(OnIdentityImplanted);
    }

    /// <summary>
    ///     Copy the custom name from the implanter's IdSaverComponent onto the implant.
    /// </summary>
    private void OnBeforeIdentityImplanted(EntityUid uid, StoredImplantIdentityComponent component, AddImplantAttemptEvent args)
    {
        if (TryComp<IdSaverComponent>(args.Implanter, out var saver))
            component.CustomName = saver.CustomName;
    }

    /// <summary>
    ///     Snapshot the implanted person's ID, so if their ID gets removed we fall back to these values.
    ///     Applies to any tracking implant (regular or command) that carries a StoredImplantIdentityComponent.
    /// </summary>
    private void OnIdentityImplanted(Entity<StoredImplantIdentityComponent> ent, ref ImplantImplantedEvent args)
    {
        if (!args.Implanted.Valid)
            return;

        var comp = ent.Comp;
        comp.JobDepartments.Clear();

        if (_idCard.TryFindIdCard(args.Implanted, out var card))
        {
            comp.Name = comp.CustomName ?? card.Comp.FullName;
            comp.Job = card.Comp.LocalizedJobTitle;
            comp.JobIcon = card.Comp.JobIcon;

            foreach (var department in card.Comp.JobDepartments)
                comp.JobDepartments.Add(Loc.GetString(_proto.Index(department).Name));
        }
        else
        {
            comp.Name = comp.CustomName;
            comp.Job = null;
            comp.JobIcon = null;
        }

        comp.Captured = true;
    }

    /// <summary>
    ///     Fall back to the identity captured at implant time for any sensor whose wearer has one, so
    ///     tracked crew don't show as unknown once their ID is removed. Applies on every console.
    /// </summary>
    private List<SuitSensorStatus> ApplyStoredIdentities(List<SuitSensorStatus> sensors)
        => sensors
            .Select(sensor => TryComp<StoredImplantIdentityComponent>(GetEntity(sensor.SuitSensorUid), out var stored) && stored.Captured
                ? WithStoredIdentity(sensor, stored)
                : sensor)
            .ToList();

    /// <summary>
    ///     Produce a copy of a sensor status with its name/job/departments replaced by the identity
    ///     captured at implant time.
    /// </summary>
    private static SuitSensorStatus WithStoredIdentity(SuitSensorStatus source, StoredImplantIdentityComponent stored)
        => new(
            source.OwnerUid,
            source.SuitSensorUid,
            stored.Name ?? source.Name,
            stored.Job ?? source.Job,
            stored.JobIcon ?? source.JobIcon,
            stored.JobDepartments.Count > 0 ? new(stored.JobDepartments) : source.JobDepartments)
        {
            Timestamp = source.Timestamp,
            Faction = source.Faction,
            IsAlive = source.IsAlive,
            TotalDamage = source.TotalDamage,
            TotalDamageThreshold = source.TotalDamageThreshold,
            Coordinates = source.Coordinates,
        };
}
