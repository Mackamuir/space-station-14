using Content.Server._Starlight.Implants;
using Content.Server.Administration;
using Content.Shared.CCVar;
using Content.Shared.Implants.Components;
using Content.Shared.Popups;
using Content.Shared.Verbs;
using Robust.Shared.Configuration;
using Robust.Shared.Player;

// ReSharper disable once CheckNamespace
namespace Content.Server.Implants;

public sealed partial class ImplanterSystem
{
    [Dependency] private QuickDialogSystem _dialog = default!;
    [Dependency] private IConfigurationManager _cfgManager = default!;

    private int _idSaverMaxNameLength;

    private void InitializeIdSaver()
    {
        SubscribeLocalEvent<IdSaverComponent, GetVerbsEvent<InteractionVerb>>(OnIdSaverVerbs);

        Subs.CVar(_cfgManager, CCVars.MaxNameLength, value => _idSaverMaxNameLength = value, true);
    }

    /// <summary>
    ///     Adds a verb to set a custom name on the implanter.
    /// </summary>
    private void OnIdSaverVerbs(EntityUid uid, IdSaverComponent component, GetVerbsEvent<InteractionVerb> args)
    {
        if (!args.CanAccess || !args.CanInteract)
            return;

        if (!TryComp<ActorComponent>(args.User, out var actor))
            return;

        if (!TryComp<ImplanterComponent>(uid, out var implanter) || !implanter.ImplanterSlot.HasItem)
            return;

        var user = args.User;
        var session = actor.PlayerSession;

        args.Verbs.Add(new InteractionVerb
        {
            Text = Loc.GetString("id-saver-set-name-verb"),
            Act = () => _dialog.OpenDialog(session,
                Loc.GetString("id-saver-set-name-verb"),
                Loc.GetString("id-saver-set-name-prompt"),
                (string name) => OnIdSaverNameEntered(uid, name, user))
        });

        if (component.CustomName != null)
        {
            args.Verbs.Add(new InteractionVerb
            {
                Text = Loc.GetString("id-saver-clear-name-verb"),
                Act = () => ClearIdSaverName(uid, user)
            });
        }
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

        component.CustomName = name;
        _popup.PopupEntity(Loc.GetString("id-saver-set-name-success", ("name", name)), uid, user);
    }

    private void ClearIdSaverName(EntityUid uid, EntityUid user)
    {
        if (!TryComp<IdSaverComponent>(uid, out var component))
            return;

        component.CustomName = null;
        _popup.PopupEntity(Loc.GetString("id-saver-clear-name-success"), uid, user);
    }
}
