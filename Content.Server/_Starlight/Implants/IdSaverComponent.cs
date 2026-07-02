namespace Content.Server._Starlight.Implants;

/// <summary>
///     STARLIGHT: Lets the holder set a custom name on the implanter before use.
///     The name is copied onto the implant when someone is implanted, overriding only the
///     stored name shown on the crew monitor.
/// </summary>
[RegisterComponent]
public sealed partial class IdSaverComponent : Component
{
    /// <summary>The custom name set via the "Set Custom Name" verb</summary>
    [DataField]
    public string? CustomName;
}
