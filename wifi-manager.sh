#!/usr/bin/env bash
# Wi-Fi Manager TUI for Ubuntu / NetworkManager
# Uses: nmcli + whiptail. Does not depend on GNOME Control Center.

set -u
export LC_ALL=C

APP_NAME="Wi-Fi Manager"
VERSION="1.0.0"
BACKTITLE="$APP_NAME $VERSION - NetworkManager TUI"
IFACE=""
TMP_FILES=()

cleanup() {
  local f
  for f in "${TMP_FILES[@]:-}"; do
    [[ -n "$f" ]] && rm -f -- "$f" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

mktemp_track() {
  local f
  f="$(mktemp)" || exit 1
  TMP_FILES+=("$f")
  printf '%s\n' "$f"
}

msg() {
  whiptail --title "$1" --backtitle "$BACKTITLE" --msgbox "$2" 18 78
}

info() {
  whiptail --title "$1" --backtitle "$BACKTITLE" --infobox "$2" 8 72
}

confirm() {
  whiptail --title "$1" --backtitle "$BACKTITLE" --yesno "$2" 12 76
}

input() {
  local title="$1" prompt="$2" default="${3:-}"
  whiptail --title "$title" --backtitle "$BACKTITLE" \
    --inputbox "$prompt" 12 76 "$default" --output-fd 1 2>/dev/null
}

password_box() {
  local title="$1" prompt="$2"
  whiptail --title "$title" --backtitle "$BACKTITLE" \
    --passwordbox "$prompt" 12 76 --output-fd 1 2>/dev/null
}

menu() {
  local title="$1" prompt="$2" height="$3" width="$4" listheight="$5"
  shift 5
  whiptail --title "$title" --backtitle "$BACKTITLE" \
    --menu "$prompt" "$height" "$width" "$listheight" "$@" \
    --output-fd 1 2>/dev/null
}

textbox_from_command() {
  local title="$1"
  shift
  local f
  f="$(mktemp_track)"
  "$@" >"$f" 2>&1 || true
  whiptail --title "$title" --backtitle "$BACKTITLE" --scrolltext --textbox "$f" 30 100
}

textbox_file() {
  local title="$1" file="$2"
  whiptail --title "$title" --backtitle "$BACKTITLE" --scrolltext --textbox "$file" 30 100
}

require_dependencies() {
  local missing=()
  local c
  for c in nmcli whiptail ip awk sed grep cut sort head tail; do
    need_cmd "$c" || missing+=("$c")
  done

  if ((${#missing[@]})); then
    printf 'Missing commands: %s\n' "${missing[*]}" >&2
    printf 'On Ubuntu, install at least: sudo apt install network-manager whiptail iproute2\n' >&2
    exit 1
  fi
}

sudo_cmd() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

nm_try() {
  # Try as current user first. If PolicyKit rejects the action, retry as root.
  local out rc
  out="$(nmcli "$@" 2>&1)"
  rc=$?
  if ((rc == 0)); then
    printf '%s\n' "$out"
    return 0
  fi

  if [[ $EUID -ne 0 ]] && need_cmd sudo; then
    out="$(sudo nmcli "$@" 2>&1)"
    rc=$?
  fi
  printf '%s\n' "$out"
  return "$rc"
}

wifi_interfaces() {
  nmcli -t --escape no --separator $'\t' -f DEVICE,TYPE,STATE device status 2>/dev/null \
    | awk -F '\t' '$2 == "wifi" {print $1 "\t" $3}'
}

choose_interface() {
  local rows=() row dev state
  while IFS=$'\t' read -r dev state; do
    [[ -n "$dev" ]] || continue
    rows+=("$dev" "$state")
  done < <(wifi_interfaces)

  if ((${#rows[@]} == 0)); then
    IFACE=""
    return 1
  fi

  if ((${#rows[@]} == 2)); then
    IFACE="${rows[0]}"
    return 0
  fi

  IFACE="$(menu "Carte Wi-Fi" "Choisis l'interface Wi-Fi a utiliser." 18 76 10 "${rows[@]}")" || return 1
  return 0
}

ensure_interface() {
  if [[ -n "$IFACE" ]] && nmcli -t -f DEVICE,TYPE device status 2>/dev/null | grep -Fq "$IFACE:wifi"; then
    return 0
  fi
  choose_interface
}

active_wifi_profile_uuid() {
  local uuid dev
  while IFS=$'\t' read -r uuid dev; do
    [[ "$dev" == "$IFACE" ]] && { printf '%s\n' "$uuid"; return 0; }
  done < <(nmcli -t --escape no --separator $'\t' -f UUID,DEVICE connection show --active 2>/dev/null)
  return 1
}

active_wifi_profile_name() {
  nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -n 1
}

find_saved_uuid_by_ssid() {
  local wanted="$1" uuid type ssid
  while IFS=$'\t' read -r uuid type ssid; do
    [[ "$type" == "802-11-wireless" ]] || continue
    [[ "$ssid" == "$wanted" ]] && { printf '%s\n' "$uuid"; return 0; }
  done < <(nmcli -t --escape no --separator $'\t' -f UUID,TYPE,802-11-wireless.ssid connection show 2>/dev/null)
  return 1
}

connection_summary() {
  local f uuid name state ip4 gw dns signal
  f="$(mktemp_track)"
  {
    echo "=== NetworkManager ==="
    nmcli general status 2>&1
    echo
    echo "=== Interface ==="
    nmcli -f GENERAL,WIFI-PROPERTIES,IP4,IP6 device show "$IFACE" 2>&1
    echo
    echo "=== Route ==="
    ip route 2>&1
    echo
    echo "=== DNS ==="
    if need_cmd resolvectl; then
      resolvectl status "$IFACE" 2>&1
    else
      cat /etc/resolv.conf 2>&1
    fi
    echo
    echo "=== Active connections ==="
    nmcli connection show --active 2>&1
    echo
    echo "=== Connectivity ==="
    nmcli networking connectivity 2>&1
  } >"$f"
  textbox_file "Etat reseau - $IFACE" "$f"
}

radio_menu() {
  local choice out
  while true; do
    choice="$(menu "Radio Wi-Fi" "Etat actuel: $(nmcli radio wifi 2>/dev/null)" 20 78 10 \
      "1" "Activer le Wi-Fi" \
      "2" "Desactiver le Wi-Fi" \
      "3" "Debloquer rfkill (soft block)" \
      "4" "Voir rfkill" \
      "5" "Redemarrer NetworkManager" \
      "0" "Retour")" || return

    case "$choice" in
      1)
        out="$(nm_try radio wifi on)"
        sleep 1
        msg "Wi-Fi" "Wi-Fi active.\n\n$out"
        ;;
      2)
        confirm "Wi-Fi" "Desactiver completement la radio Wi-Fi ?" || continue
        out="$(nm_try radio wifi off)"
        msg "Wi-Fi" "$out"
        ;;
      3)
        if ! need_cmd rfkill; then
          msg "rfkill absent" "Installe le paquet rfkill :\n\nsudo apt install rfkill"
          continue
        fi
        out="$(sudo_cmd rfkill unblock wifi 2>&1)"
        nm_try radio wifi on >/dev/null 2>&1 || true
        msg "rfkill" "Deblocage demande.\n\n$out\n\n$(rfkill list 2>&1)"
        ;;
      4)
        if need_cmd rfkill; then
          textbox_from_command "rfkill" rfkill list all
        else
          msg "rfkill absent" "Commande rfkill absente."
        fi
        ;;
      5)
        confirm "NetworkManager" "Redemarrer NetworkManager ? Les connexions seront interrompues quelques secondes." || continue
        out="$(sudo_cmd systemctl restart NetworkManager 2>&1)"
        sleep 2
        IFACE=""
        choose_interface >/dev/null 2>&1 || true
        msg "NetworkManager" "Service redemarre.\n\n$out"
        ;;
      0) return ;;
    esac
  done
}

connect_selected_ap() {
  local ssid="$1" bssid="$2" security="$3" saved_uuid out pass

  saved_uuid="$(find_saved_uuid_by_ssid "$ssid" 2>/dev/null || true)"
  if [[ -n "$saved_uuid" ]]; then
    info "Connexion" "Activation du profil enregistre pour:\n$ssid"
    out="$(nm_try --wait 35 connection up uuid "$saved_uuid" ifname "$IFACE" ap "$bssid")"
    if [[ $? -eq 0 ]]; then
      msg "Connecte" "Connexion etablie avec $ssid.\n\n$out"
      return 0
    fi

    if confirm "Echec du profil" "Le profil enregistre a echoue.\n\n$out\n\nLe supprimer et recreer la connexion ?"; then
      nm_try connection delete uuid "$saved_uuid" >/dev/null 2>&1 || true
    else
      return 1
    fi
  fi

  if [[ -z "$security" || "$security" == "--" || "$security" == "NONE" ]]; then
    info "Connexion" "Connexion au reseau ouvert:\n$ssid"
    out="$(nm_try --wait 35 device wifi connect "$ssid" ifname "$IFACE" bssid "$bssid")"
  elif [[ "$security" == *"802.1X"* || "$security" == *"EAP"* ]]; then
    msg "Reseau entreprise" "Ce reseau utilise 802.1X/EAP. Le script peut activer un profil entreprise deja configure, mais ne cree pas automatiquement les certificats/parametres EAP."
    return 1
  else
    pass="$(password_box "Mot de passe Wi-Fi" "Mot de passe pour:\n$ssid\n\nSecurite: $security")" || return 1
    [[ -n "$pass" ]] || { msg "Mot de passe" "Mot de passe vide."; return 1; }
    info "Connexion" "Connexion a:\n$ssid"
    out="$(nm_try --wait 35 device wifi connect "$ssid" password "$pass" ifname "$IFACE" bssid "$bssid")"
    pass=""
  fi

  if [[ $? -eq 0 ]]; then
    msg "Connecte" "Connexion etablie avec $ssid.\n\n$out"
    return 0
  fi

  msg "Echec de connexion" "$out"
  return 1
}

scan_and_connect() {
  ensure_interface || {
    msg "Aucune carte Wi-Fi" "NetworkManager ne voit aucune interface Wi-Fi. Ouvre Diagnostics/Reparation depuis le menu principal."
    return
  }

  nm_try radio wifi on >/dev/null 2>&1 || true
  info "Scan Wi-Fi" "Recherche des reseaux avec $IFACE..."
  nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
  sleep 2

  local raw=() line inuse ssid bssid signal bars security freq idx choice
  local menu_items=() ssids=() bssids=() securities=()

  while IFS=$'\t' read -r inuse ssid bssid signal bars security freq; do
    [[ -n "$bssid" ]] || continue
    [[ -n "$ssid" ]] || ssid="<SSID masque>"
    idx="${#ssids[@]}"
    ssids+=("$ssid")
    bssids+=("$bssid")
    securities+=("$security")
    [[ "$inuse" == "*" ]] && inuse="CONNECTE" || inuse=""
    menu_items+=("$idx" "${inuse:+[$inuse] }$ssid | ${signal}% $bars | ${security:-ouvert} | ${freq} MHz | $bssid")
  done < <(nmcli -t --escape no --separator $'\t' \
      -f IN-USE,SSID,BSSID,SIGNAL,BARS,SECURITY,FREQ \
      device wifi list --rescan yes ifname "$IFACE" 2>/dev/null)

  if ((${#ssids[@]} == 0)); then
    msg "Aucun reseau" "Aucun point d'acces n'a ete trouve sur $IFACE.\n\nUtilise Diagnostics/Reparation pour verifier rfkill, le pilote et le firmware."
    return
  fi

  choice="$(menu "Reseaux Wi-Fi - $IFACE" "Choisis un point d'acces. Les doublons correspondent a plusieurs bornes/BSSID." 28 110 18 "${menu_items[@]}")" || return
  idx="$choice"

  if [[ "${ssids[$idx]}" == "<SSID masque>" ]]; then
    hidden_network
    return
  fi

  connect_selected_ap "${ssids[$idx]}" "${bssids[$idx]}" "${securities[$idx]}"
}

hidden_network() {
  ensure_interface || { msg "Aucune carte Wi-Fi" "Aucune interface Wi-Fi geree par NetworkManager."; return; }

  local ssid security pass out
  ssid="$(input "Reseau masque" "Nom exact du SSID masque :")" || return
  [[ -n "$ssid" ]] || return

  security="$(menu "Securite" "Type de securite du reseau masque." 18 76 8 \
    "open" "Reseau ouvert" \
    "wpa" "WPA/WPA2/WPA3 personnel (mot de passe)" \
    "enterprise" "802.1X / EAP entreprise")" || return

  case "$security" in
    open)
      out="$(nm_try --wait 35 device wifi connect "$ssid" ifname "$IFACE" hidden yes)"
      ;;
    wpa)
      pass="$(password_box "Mot de passe" "Mot de passe pour $ssid :")" || return
      [[ -n "$pass" ]] || return
      out="$(nm_try --wait 35 device wifi connect "$ssid" password "$pass" ifname "$IFACE" hidden yes)"
      pass=""
      ;;
    enterprise)
      msg "802.1X" "La creation automatique d'un profil EAP est volontairement exclue: elle depend du type EAP, de l'identite et souvent de certificats. Un profil existant reste activable dans Reseaux enregistres."
      return
      ;;
  esac

  if [[ $? -eq 0 ]]; then
    msg "Connecte" "Connexion etablie avec $ssid.\n\n$out"
  else
    msg "Echec" "$out"
  fi
}

saved_networks() {
  local items=() name uuid type auto ssid choice action out

  while IFS=$'\t' read -r name uuid type auto ssid; do
    [[ "$type" == "802-11-wireless" ]] || continue
    items+=("$uuid" "${ssid:-$name} | profil: $name | autoconnect: $auto")
  done < <(nmcli -t --escape no --separator $'\t' \
      -f NAME,UUID,TYPE,AUTOCONNECT,802-11-wireless.ssid connection show 2>/dev/null)

  if ((${#items[@]} == 0)); then
    msg "Reseaux enregistres" "Aucun profil Wi-Fi enregistre."
    return
  fi

  choice="$(menu "Reseaux enregistres" "Choisis un profil Wi-Fi." 26 100 16 "${items[@]}")" || return

  while true; do
    name="$(nmcli -g connection.id connection show uuid "$choice" 2>/dev/null | head -n1)"
    ssid="$(nmcli -g 802-11-wireless.ssid connection show uuid "$choice" 2>/dev/null | head -n1)"
    action="$(menu "$ssid" "Profil: $name" 22 80 12 \
      "1" "Connecter maintenant" \
      "2" "Voir les details" \
      "3" "Activer l'autoconnect" \
      "4" "Desactiver l'autoconnect" \
      "5" "Priorite d'autoconnect" \
      "6" "Oublier ce reseau" \
      "0" "Retour")" || return

    case "$action" in
      1)
        ensure_interface || { msg "Wi-Fi" "Aucune interface Wi-Fi."; continue; }
        out="$(nm_try --wait 35 connection up uuid "$choice" ifname "$IFACE")"
        [[ $? -eq 0 ]] && msg "Connecte" "$out" || msg "Echec" "$out"
        ;;
      2)
        textbox_from_command "Profil $name" nmcli --show-secrets connection show uuid "$choice"
        ;;
      3)
        out="$(nm_try connection modify uuid "$choice" connection.autoconnect yes)"
        msg "Autoconnect" "$out"
        ;;
      4)
        out="$(nm_try connection modify uuid "$choice" connection.autoconnect no)"
        msg "Autoconnect" "$out"
        ;;
      5)
        local_prio="$(input "Priorite" "Priorite d'autoconnect (-999 a 999). Une valeur plus haute est preferee." "0")" || continue
        [[ "$local_prio" =~ ^-?[0-9]+$ ]] || { msg "Valeur invalide" "Entre un nombre entier."; continue; }
        out="$(nm_try connection modify uuid "$choice" connection.autoconnect-priority "$local_prio")"
        msg "Priorite" "$out"
        ;;
      6)
        confirm "Oublier $ssid" "Supprimer definitivement ce profil Wi-Fi et son mot de passe enregistre ?" || continue
        out="$(nm_try connection delete uuid "$choice")"
        msg "Profil supprime" "$out"
        return
        ;;
      0) return ;;
    esac
  done
}

disconnect_menu() {
  ensure_interface || { msg "Wi-Fi" "Aucune interface Wi-Fi."; return; }
  local choice out
  choice="$(menu "Connexion - $IFACE" "Profil actif: $(active_wifi_profile_name)" 18 76 8 \
    "1" "Deconnecter l'interface" \
    "2" "Reconnecter automatiquement" \
    "3" "Rescanner les reseaux" \
    "0" "Retour")" || return

  case "$choice" in
    1)
      out="$(nm_try device disconnect "$IFACE")"
      msg "Deconnexion" "$out"
      ;;
    2)
      out="$(nm_try device connect "$IFACE")"
      msg "Reconnexion" "$out"
      ;;
    3)
      nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
      msg "Scan" "Nouveau scan demande."
      ;;
  esac
}

apply_connection_again() {
  local uuid="$1" out
  info "Application" "Reactivation du profil reseau..."
  out="$(nm_try --wait 35 connection up uuid "$uuid" ifname "$IFACE")"
  [[ $? -eq 0 ]] && msg "Configuration appliquee" "$out" || msg "Echec" "$out"
}

ip_dns_menu() {
  ensure_interface || { msg "IP / DNS" "Aucune interface Wi-Fi."; return; }
  local uuid choice dns addr gw out dns_csv
  uuid="$(active_wifi_profile_uuid 2>/dev/null || true)"
  [[ -n "$uuid" ]] || { msg "IP / DNS" "Aucune connexion Wi-Fi active sur $IFACE."; return; }

  while true; do
    choice="$(menu "IP / DNS" "Configuration du profil Wi-Fi actif." 24 88 14 \
      "1" "Voir IPv4 / IPv6 / DNS" \
      "2" "IPv4 automatique (DHCP)" \
      "3" "Definir des DNS IPv4 manuels" \
      "4" "Remettre les DNS IPv4 automatiques" \
      "5" "Configurer une IPv4 statique" \
      "6" "IPv6 automatique" \
      "7" "Desactiver IPv6 pour ce profil" \
      "0" "Retour")" || return

    case "$choice" in
      1)
        textbox_from_command "IP / DNS" nmcli -f ipv4,ipv6 connection show uuid "$uuid"
        ;;
      2)
        out="$(nm_try connection modify uuid "$uuid" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" ipv4.ignore-auto-dns no)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      3)
        dns="$(input "DNS IPv4" "Serveurs DNS separes par des virgules.\nExemple: 1.1.1.1,9.9.9.9")" || continue
        [[ -n "$dns" ]] || continue
        dns_csv="${dns//,/ }"
        out="$(nm_try connection modify uuid "$uuid" ipv4.dns "$dns_csv" ipv4.ignore-auto-dns yes)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      4)
        out="$(nm_try connection modify uuid "$uuid" ipv4.dns "" ipv4.ignore-auto-dns no)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      5)
        addr="$(input "IPv4 statique" "Adresse avec prefixe CIDR.\nExemple: 192.168.1.50/24")" || continue
        [[ -n "$addr" ]] || continue
        gw="$(input "Passerelle" "Passerelle IPv4.\nExemple: 192.168.1.1")" || continue
        [[ -n "$gw" ]] || continue
        dns="$(input "DNS" "DNS separes par des virgules.\nExemple: 1.1.1.1,9.9.9.9")" || continue
        dns_csv="${dns//,/ }"
        confirm "IPv4 statique" "Appliquer:\n\nAdresse: $addr\nPasserelle: $gw\nDNS: $dns_csv\n\nUne mauvaise valeur peut couper la connexion." || continue
        out="$(nm_try connection modify uuid "$uuid" ipv4.method manual ipv4.addresses "$addr" ipv4.gateway "$gw" ipv4.dns "$dns_csv" ipv4.ignore-auto-dns yes)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      6)
        out="$(nm_try connection modify uuid "$uuid" ipv6.method auto)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      7)
        confirm "IPv6" "Desactiver IPv6 uniquement pour ce profil Wi-Fi ?" || continue
        out="$(nm_try connection modify uuid "$uuid" ipv6.method disabled)"
        [[ $? -eq 0 ]] && apply_connection_again "$uuid" || msg "Echec" "$out"
        ;;
      0) return ;;
    esac
  done
}

diagnostics_report() {
  local f
  f="$(mktemp_track)"
  {
    echo "Wi-Fi Manager diagnostics - $(date -Is)"
    echo
    echo "=== OS ==="
    if [[ -r /etc/os-release ]]; then cat /etc/os-release; fi
    echo "Kernel: $(uname -a)"
    echo
    echo "=== NetworkManager ==="
    nmcli general status 2>&1
    echo
    nmcli radio all 2>&1
    echo
    nmcli device status 2>&1
    echo
    echo "=== Wi-Fi access points ==="
    nmcli device wifi list 2>&1
    echo
    echo "=== rfkill ==="
    if need_cmd rfkill; then rfkill list all 2>&1; else echo "rfkill absent"; fi
    echo
    echo "=== iw dev ==="
    if need_cmd iw; then iw dev 2>&1; else echo "iw absent"; fi
    echo
    echo "=== ip link ==="
    ip -br link 2>&1
    echo
    echo "=== ip addr ==="
    ip -br addr 2>&1
    echo
    echo "=== route ==="
    ip route 2>&1
    echo
    echo "=== PCI network devices / drivers ==="
    if need_cmd lspci; then lspci -nnk 2>&1 | grep -iA4 -E 'network|wireless|ethernet'; else echo "lspci absent"; fi
    echo
    echo "=== USB ==="
    if need_cmd lsusb; then lsusb 2>&1; else echo "lsusb absent"; fi
    echo
    echo "=== Wi-Fi kernel modules ==="
    lsmod 2>&1 | grep -iE 'iwlwifi|iwlmvm|rtw|rtl|brcm|b43|ath|mt76|cfg80211|mac80211' || true
    echo
    echo "=== NetworkManager log (current boot) ==="
    journalctl -b -u NetworkManager --no-pager -n 100 2>&1 || true
  } >"$f"
  textbox_file "Diagnostic complet" "$f"
}

safe_repair() {
  local detected="" out=""
  confirm "Reparation sure" "Cette action va:\n- debloquer rfkill en logiciel\n- activer la radio Wi-Fi\n- remettre l'interface en mode managed si elle existe\n- redemarrer NetworkManager\n- relancer un scan\n\nContinuer ?" || return

  if need_cmd rfkill; then
    out+="$(sudo_cmd rfkill unblock wifi 2>&1)"$'\n'
  fi

  nm_try radio wifi on >/dev/null 2>&1 || true

  if [[ -z "$IFACE" ]] && need_cmd iw; then
    detected="$(iw dev 2>/dev/null | awk '$1 == "Interface" {print $2; exit}')"
    [[ -n "$detected" ]] && IFACE="$detected"
  fi

  if [[ -n "$IFACE" ]]; then
    out+="$(nm_try device set "$IFACE" managed yes 2>&1)"$'\n'
  fi

  out+="$(sudo_cmd systemctl restart NetworkManager 2>&1)"$'\n'
  sleep 2

  if [[ -n "$IFACE" ]]; then
    nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
  fi

  choose_interface >/dev/null 2>&1 || true
  msg "Reparation terminee" "Actions terminees.\n\n$out\nEtat:\n$(nmcli device status 2>&1)"
}

deep_kernel_diagnostic() {
  local f
  f="$(mktemp_track)"
  {
    echo "=== dmesg Wi-Fi / firmware / driver ==="
    if [[ $EUID -eq 0 ]]; then
      dmesg 2>&1
    else
      sudo dmesg 2>&1
    fi | grep -iE 'wifi|wireless|wlan|firmware|iwlwifi|iwlmvm|rtw|rtl|brcm|b43|ath|mt76|cfg80211' | tail -n 180
  } >"$f"
  textbox_file "Diagnostic noyau / firmware" "$f"
}

diagnostics_menu() {
  local choice
  while true; do
    choice="$(menu "Diagnostics / Reparation" "Utilise ces outils si le scan ne retourne aucun Wi-Fi." 24 90 14 \
      "1" "Rapport de diagnostic complet" \
      "2" "Reparation sure NetworkManager/rfkill" \
      "3" "Diagnostic noyau / firmware (sudo)" \
      "4" "Afficher les interfaces Wi-Fi detectees par iw" \
      "5" "Afficher les peripheriques PCI reseau" \
      "6" "Verifier le service NetworkManager" \
      "0" "Retour")" || return

    case "$choice" in
      1) diagnostics_report ;;
      2) safe_repair ;;
      3) deep_kernel_diagnostic ;;
      4)
        if need_cmd iw; then textbox_from_command "iw dev" iw dev; else msg "iw absent" "Installe iw avec: sudo apt install iw"; fi
        ;;
      5)
        if need_cmd lspci; then
          local f
          f="$(mktemp_track)"
          lspci -nnk 2>&1 | grep -iA4 -E 'network|wireless|ethernet' >"$f" || true
          textbox_file "PCI reseau" "$f"
        else
          msg "lspci absent" "Installe pciutils: sudo apt install pciutils"
        fi
        ;;
      6)
        textbox_from_command "NetworkManager" systemctl status NetworkManager --no-pager
        ;;
      0) return ;;
    esac
  done
}

install_app() {
  local src desktop
  src="$(readlink -f "$0")"
  [[ -f "$src" ]] || { echo "Impossible de determiner le chemin du script." >&2; exit 1; }

  if [[ $EUID -ne 0 ]]; then
    exec sudo "$src" --install
  fi

  install -m 0755 "$src" /usr/local/bin/wifi-manager
  cat >/usr/share/applications/wifi-manager.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Wi-Fi Manager
Comment=Gestionnaire Wi-Fi NetworkManager en terminal
Exec=/usr/local/bin/wifi-manager
Icon=network-wireless
Terminal=true
Categories=System;Settings;Network;
Keywords=wifi;wireless;network;networkmanager;
StartupNotify=false
DESKTOP
  chmod 0644 /usr/share/applications/wifi-manager.desktop
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  echo "Installe:"
  echo "  commande: wifi-manager"
  echo "  application: Wi-Fi Manager"
  exit 0
}

uninstall_app() {
  if [[ $EUID -ne 0 ]]; then
    exec sudo "$(readlink -f "$0")" --uninstall
  fi
  rm -f /usr/local/bin/wifi-manager /usr/share/applications/wifi-manager.desktop
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  echo "Wi-Fi Manager desinstalle."
  exit 0
}

about() {
  msg "A propos" "$APP_NAME $VERSION\n\nInterface TUI independante de GNOME Control Center.\n\nBackend: NetworkManager / nmcli.\n\nFonctions:\n- scan et connexion Wi-Fi\n- profils enregistres\n- reseaux masques\n- autoconnect/priorite\n- DHCP, IPv4 statique, DNS, IPv6\n- rfkill et radio\n- diagnostics driver/firmware\n- reparation NetworkManager\n\nLes nouveaux profils 802.1X/EAP complexes ne sont pas generes automatiquement."
}

main_menu() {
  local choice iface_text
  choose_interface >/dev/null 2>&1 || true

  while true; do
    iface_text="${IFACE:-aucune detectee}"
    choice="$(menu "$APP_NAME" "Interface: $iface_text | Wi-Fi: $(nmcli radio wifi 2>/dev/null)" 28 92 17 \
      "1" "Scanner et se connecter a un Wi-Fi" \
      "2" "Se connecter a un reseau masque" \
      "3" "Gerer les reseaux Wi-Fi enregistres" \
      "4" "Deconnexion / reconnexion" \
      "5" "Etat IP / route / DNS / connexion" \
      "6" "Configurer IP et DNS" \
      "7" "Radio Wi-Fi / rfkill / NetworkManager" \
      "8" "Diagnostics et reparation" \
      "9" "Changer de carte Wi-Fi" \
      "10" "A propos" \
      "0" "Quitter")" || exit 0

    case "$choice" in
      1) scan_and_connect ;;
      2) hidden_network ;;
      3) saved_networks ;;
      4) disconnect_menu ;;
      5)
        ensure_interface && connection_summary || msg "Wi-Fi" "Aucune interface Wi-Fi detectee par NetworkManager."
        ;;
      6) ip_dns_menu ;;
      7) radio_menu ;;
      8) diagnostics_menu ;;
      9) IFACE=""; choose_interface || msg "Wi-Fi" "Aucune interface Wi-Fi geree par NetworkManager." ;;
      10) about ;;
      0) exit 0 ;;
    esac
  done
}

case "${1:-}" in
  --install) install_app ;;
  --uninstall) uninstall_app ;;
  --version) printf '%s %s\n' "$APP_NAME" "$VERSION"; exit 0 ;;
  -h|--help)
    cat <<HELP
$APP_NAME $VERSION
Usage:
  $0              Lance l'interface TUI
  $0 --install    Installe /usr/local/bin/wifi-manager + lanceur .desktop
  $0 --uninstall  Desinstalle le lanceur et la commande
  $0 --version    Affiche la version
HELP
    exit 0
    ;;
esac

require_dependencies
main_menu
