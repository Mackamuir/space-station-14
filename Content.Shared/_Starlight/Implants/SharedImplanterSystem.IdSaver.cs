using Content.Shared._Starlight.Implants.Components;
using Content.Shared.Implants.Components;
using Content.Shared.Verbs;

// ReSharper disable once CheckNamespace
namespace Content.Shared.Implants;

public abstract partial class SharedImplanterSystem
{
    private void InitializeIdSaver() => SubscribeLocalEvent<IdSaverComponent, GetVerbsEvent<InteractionVerb>>(OnIdSaverVerbs);

    private void OnIdSaverVerbs(EntityUid uid, IdSaverComponent component, GetVerbsEvent<InteractionVerb> args)
    {
        if (!args.CanAccess || !args.CanInteract)
            return;

        if (!TryComp<ImplanterComponent>(uid, out var implanter) || !implanter.ImplanterSlot.HasItem)
            return;

        var user = args.User;

        args.Verbs.Add(new InteractionVerb
        {
            Text = Loc.GetString("id-saver-set-name-verb"),
            Act = () => OpenIdSaverNameDialog(uid, user)
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

    /// <summary>
    ///     Opens the name-entry dialog. No-op on the client; the server override shows the dialog.
    /// </summary>
    protected virtual void OpenIdSaverNameDialog(EntityUid uid, EntityUid user)
    {
    }

    public void SetIdSaverName(EntityUid uid, string name, IdSaverComponent? component = null)
    {
        if (!Resolve(uid, ref component))
            return;

        component.CustomName = name;
        Dirty(uid, component);
    }

    public void ClearIdSaverName(EntityUid uid, EntityUid user, IdSaverComponent? component = null)
    {
        if (!Resolve(uid, ref component))
            return;

        component.CustomName = null;
        Dirty(uid, component);
        _popup.PopupClient(Loc.GetString("id-saver-clear-name-success"), uid, user);
    }
}
