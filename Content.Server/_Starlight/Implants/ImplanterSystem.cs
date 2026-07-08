using Content.Server.Administration;
using Content.Shared._Starlight.Implants.Components;
using Content.Shared.CCVar;
using Content.Shared.Popups;
using Robust.Shared.Configuration;
using Robust.Shared.Player;

// ReSharper disable once CheckNamespace
namespace Content.Server.Implants;

public sealed partial class ImplanterSystem
{
    [Dependency] private QuickDialogSystem _dialog = default!;
    [Dependency] private IConfigurationManager _cfgManager = default!;

    private int _idSaverMaxNameLength;

    private void InitializeIdSaverServer()
    {
        Subs.CVar(_cfgManager, CCVars.MaxNameLength, value => _idSaverMaxNameLength = value, true);
    }

    protected override void OpenIdSaverNameDialog(EntityUid uid, EntityUid user)
    {
        if (!TryComp<ActorComponent>(user, out var actor))
            return;

        _dialog.OpenDialog(actor.PlayerSession,
            Loc.GetString("id-saver-set-name-verb"),
            Loc.GetString("id-saver-set-name-prompt"),
            (string name) => OnIdSaverNameEntered(uid, name, user));
    }

    private void OnIdSaverNameEntered(EntityUid uid, string name, EntityUid user)
    {
        if (!TryComp<IdSaverComponent>(uid, out var component))
            return;

        name = name.Trim();

        if (name.Length == 0 || name.Length > _idSaverMaxNameLength)
        {
            _popup.PopupEntity(Loc.GetString("id-saver-set-name-invalid", ("max", _idSaverMaxNameLength)), uid, user, PopupType.SmallCaution);
            return;
        }

        SetIdSaverName(uid, name, component);
        _popup.PopupEntity(Loc.GetString("id-saver-set-name-success", ("name", name)), uid, user);
    }
}
