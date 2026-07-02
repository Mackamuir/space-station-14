namespace Content.Server._Starlight.Medical.CrewMonitoring;

/// <summary>
///     STARLIGHT: Records the ID of whoever gets implanted by a tracking implant (regular or command)
/// </summary>
[RegisterComponent]
public sealed partial class StoredImplantIdentityComponent : Component
{
    /// <summary>Whether an identity has been captured yet.</summary>
    [DataField]
    public bool Captured;

    [DataField]
    public string? Name;

    [DataField]
    public string? Job;

    [DataField]
    public string? JobIcon;

    [DataField]
    public List<string> JobDepartments = new();
}
