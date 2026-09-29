import argparse


def _subparser_action(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _commands(parser):
    action = _subparser_action(parser)
    if action is None:
        raise ValueError("parser has no subcommands to complete")
    ordered = list(action.choices)
    helps = {}
    for pseudo in getattr(action, "_choices_actions", []):
        helps[pseudo.dest] = pseudo.help or ""
    details = {}
    for name in ordered:
        subparser = action.choices[name]
        rows = []
        for sub_action in subparser._actions:
            if sub_action.option_strings:
                rows.append((list(sub_action.option_strings), sub_action.help or ""))
        details[name] = rows
    return ordered, details, helps


def _bash(names, details, parser):
    global_opts = sorted({opt for action in parser._actions
                          for opt in action.option_strings})
    lines = [
        "_espbench() {",
        '    local cur command opts',
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        '    if [ "$COMP_CWORD" -eq 1 ]; then',
        "        COMPREPLY=( $(compgen -W \""
        + " ".join([*names, *global_opts])
        + "\" -- \"$cur\") )",
        "        return 0",
        "    fi",
        '    command="${COMP_WORDS[1]}"',
        '    case "$command" in',
    ]
    for name in names:
        opts = " ".join(sorted({opt for options, _ in details[name]
                                for opt in options}))
        lines.append(f"        {name}) opts=\"{opts}\" ;;")
    lines += [
        "        *) opts=\"\" ;;",
        "    esac",
        "    COMPREPLY=( $(compgen -W \"$opts\" -- \"$cur\") )",
        "}",
        "complete -F _espbench espbench",
    ]
    return "\n".join(lines)


def _zsh(names, details, helps):
    lines = ["#compdef espbench", "_espbench() {", "    local -a commands"]
    lines.append("    commands=(" + " ".join(
        f"'{name}:{helps.get(name, name)}'" for name in names) + ")")
    lines += [
        "    if (( CURRENT == 2 )); then",
        "        _describe 'command' commands",
        "        return",
        "    fi",
        "    local -a opts",
        "    case \"${words[2]}\" in",
    ]
    for name in names:
        opts = sorted({opt for options, _ in details[name] for opt in options})
        lines.append(f"        {name}) opts=({' '.join(opts)}) ;;")
    lines += [
        "        *) opts=() ;;",
        "    esac",
        "    compadd -- $opts",
        "}",
        '_espbench "$@"',
    ]
    return "\n".join(lines)


def _fish(names, details, helps):
    lines = [
        "function __espbench_no_subcommand",
        "    not __fish_seen_subcommand_from " + " ".join(names),
        "end",
    ]
    for name in names:
        description = helps.get(name, name).replace("'", "")
        lines.append(
            f"complete -c espbench -f -n '__espbench_no_subcommand' "
            f"-a {name} -d '{description}'")
    for name in names:
        for options, help_text in details[name]:
            condition = f"__fish_seen_subcommand_from {name}"
            description = help_text.replace("'", "")
            for option in options:
                if option.startswith("--"):
                    flag = f"-l {option[2:]}"
                elif option.startswith("-") and len(option) == 2:
                    flag = f"-s {option[1:]}"
                else:
                    continue
                suffix = f" -d '{description}'" if description else ""
                lines.append(
                    f"complete -c espbench -n '{condition}' {flag}{suffix}")
    return "\n".join(lines)


def completion_script(shell, parser):
    names, details, helps = _commands(parser)
    if shell == "bash":
        return _bash(names, details, parser)
    if shell == "zsh":
        return _zsh(names, details, helps)
    if shell == "fish":
        return _fish(names, details, helps)
    raise ValueError(f"unsupported shell {shell!r}; expected bash, zsh or fish")
