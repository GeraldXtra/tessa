if [ -n "$TESSA_SHELL_LOGIN" ]; then
  [ -r /etc/profile ] && . /etc/profile
  if [ -r "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile"
  elif [ -r "$HOME/.bash_login" ]; then . "$HOME/.bash_login"
  elif [ -r "$HOME/.profile" ]; then . "$HOME/.profile"
  fi
fi

__tessa_mode=tessa
[ "$TESSA_PROMPT" = shell ] && __tessa_mode=shell
__tessa_sf="${TESSA_SGR_FOLDER:-0}"
__tessa_sd="${TESSA_SGR_DIM:-0}"
__tessa_si="${TESSA_SGR_INPUT:-0}"
__tessa_banner="$TESSA_BANNER"
unset TESSA_PROMPT TESSA_SGR_FOLDER TESSA_SGR_DIM TESSA_SGR_INPUT TESSA_BANNER TESSA_SHELL_LOGIN

__tessa_first=1
__tessa_ran=0
__tessa_nl=
__tessa_wp=
__tessa_folder=
__tessa_br=
__tessa_own="$PS1"
__tessa_root="$(cygpath -m / 2>/dev/null)"
__tessa_root="${__tessa_root%/}"
__tessa_od=
for __tessa_k in "$OneDrive" "$OneDriveConsumer" "$OneDriveCommercial"; do
  if [[ $__tessa_k =~ ^([A-Za-z]):(.*)$ ]]; then
    __tessa_od="/${BASH_REMATCH[1],,}${BASH_REMATCH[2]//\\//}"
    break
  fi
done
unset __tessa_k

__tessa_winpath() {
  local p="$1"
  if [[ $p =~ ^/([a-zA-Z])(/.*)?$ ]]; then
    local rest="${BASH_REMATCH[2]}"
    __tessa_wp="${BASH_REMATCH[1]^^}:${rest:-/}"
  elif [[ $p =~ ^[a-zA-Z]: ]]; then
    __tessa_wp="$p"
  else
    __tessa_wp="${__tessa_root}${p}"
  fi
  __tessa_wp="${__tessa_wp//\//\\}"
}

__tessa_branch() {
  __tessa_br=
  local d="$PWD" g= h= t=
  if [[ -n $__tessa_od ]]; then
    case "${PWD,,}" in "${__tessa_od,,}"|"${__tessa_od,,}"/*) return ;; esac
  fi
  while :; do
    if [[ -d "$d/.git" ]]; then h="$d/.git/HEAD"; break
    elif [[ -f "$d/.git" ]]; then
      IFS= read -r g < "$d/.git" || true
      g="${g#gitdir: }"
      if [[ $g != /* && ! $g =~ ^[A-Za-z]: ]]; then g="$d/$g"; fi
      h="$g/HEAD"
      break
    fi
    [[ -z $d || $d == / ]] && return
    d="${d%/*}"
  done
  [[ -r $h ]] || return
  IFS= read -r t < "$h" || true
  if [[ $t == "ref: refs/heads/"* ]]; then __tessa_br="${t#ref: refs/heads/}"
  elif [[ $t =~ ^[0-9a-fA-F]{7,}$ ]]; then __tessa_br="${t:0:7}"
  fi
}

__tessa_pc() {
  local code=$?
  local out=
  if [[ $__tessa_ran == 1 ]]; then
    __tessa_ran=0
    out+=$'\e]133;D;'"$code"$'\a'
  fi
  __tessa_winpath "$PWD"
  local u="${__tessa_wp//\\//}"
  u="${u//%/%25}"; u="${u// /%20}"; u="${u//#/%23}"; u="${u//\?/%3F}"; u="${u//;/%3B}"
  out+=$'\e]7;file://localhost/'"$u"$'\a'
  printf '%s' "$out"
  if [[ $__tessa_mode == tessa ]]; then
    __tessa_folder="${__tessa_wp##*\\}"
    [[ -z $__tessa_folder ]] && __tessa_folder="$__tessa_wp"
    __tessa_branch
  fi
  if (( __tessa_first )); then __tessa_first=0; __tessa_nl=; else __tessa_nl=$'\n'; fi
  return $code
}

if [[ $__tessa_mode == tessa ]]; then
  PS1='${__tessa_nl}\[\e]133;A\a\e[${__tessa_sf}m\]${__tessa_folder}\[\e[0m\]${__tessa_br:+  \[\e[${__tessa_sd}m\]${__tessa_br}\[\e[0m\]}  \[\e[${__tessa_sd}m\]>\[\e[0m\] \[\e]133;B\a\e[${__tessa_si}m\]'
else
  PS1='\[\e]133;A\a\]'"$__tessa_own"'\[\e]133;B\a\]'
fi
PS0=$'\e[0m\e]133;C\a''${__tessa_nul[__tessa_ran=1]}'

if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
  PROMPT_COMMAND=(__tessa_pc "${PROMPT_COMMAND[@]}")
else
  PROMPT_COMMAND="__tessa_pc${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi

if [[ -n $__tessa_banner ]]; then
  printf '\e[%sm%s\e[0m\n' "$__tessa_sd" "$__tessa_banner"
  __tessa_first=0
fi
unset __tessa_banner
