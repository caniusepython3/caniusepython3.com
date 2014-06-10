Stouts.python
=============

[![Build Status](https://travis-ci.org/Stouts/Stouts.python.png)](https://travis-ci.org/Stouts/Stouts.python)

Ansible role which manage python (pip, virtualenv)

#### Variables

```yaml
python_enabled: yes                 # The role is enabled
python_ppa: ppa:fkrull/deadsnakes   # Python PPA
python_version: 2.7                 # Set version (2.6, 2.7, 3.3, 3.4)
python_pip_executable: pip{{python_version}}
python_pip_latest:                  # Update python packages
- setuptools
- virtualenv
```

#### Usage

Add `Stouts.python` to your roles and set vars in your playbook file.

Example:

```yaml

- hosts: all

  roles:
    - Stouts.python

  vars:
    python_version: 3.4
```

#### License

Licensed under the MIT License. See the LICENSE file for details.

#### Feedback, bug-reports, requests, ...

Are [welcome](https://github.com/Stouts/Stouts.python/issues)!
