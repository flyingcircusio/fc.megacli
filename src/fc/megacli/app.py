import argparse
import hurry.filesize
import megacli
import os
import re
import subprocess
import terminaltables


def summary():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--megacli_path',
        default='/opt/bin/MegaCli',
        help='Path to MegaCli or MegaCli64 (default %(default)s)'
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        parser.error('Must run as root!')

    # connect to Avago/LSI adapter
    cli = megacli.MegaCLI(args.megacli_path)
    adapters = cli.adapters()
    logicaldrives = cli.logicaldrives()
    physicaldrives = cli.physicaldrives()

    # gather disk information provided by OS
    disks_by_path = subprocess.check_output(['ls', '-l', '/dev/disk/by-path/'])

    # define summary table layout
    table_data = [
        ['Linux\nDevice',
         'Linux\nMountpoints',
         'LSI LD',
         'LSI\nLD\nStatus',
         'LSI PDs\n[E:S] Size (Inquiry data)',
         'LSI\nPD\nStatus']
    ]

    # create LD to PD mapping
    logical_to_physical = subprocess.check_output([args.megacli_path, '-LdPdInfo', '-aALL'])
    ld_to_pd = {}
    for line in logical_to_physical.splitlines():
        match = re.search('^Virtual Drive: ([0-9][0-9]*).*$|^Enclosure Device ID: ([0-9][0-9]*).*$|^Slot Number: ([0-9][0-9]*).*$', line)
        if match:
            if match.group(1):
                current_ld = match.group(1)
                if not current_ld in ld_to_pd:
                    ld_to_pd[current_ld] = []
            elif match.group(3):
                ld_to_pd[current_ld].append(match.group(3))


    def get_physicaldrive_by_param(physicaldrives, param, value):
        for drive in physicaldrives:
            if str(drive[param]) == value:
                return drive

    def get_logicaldrive_param(logicaldevice_id, param):
        for ld in logicaldrives:
            if logicaldevice_id == str(ld['id']):
                return ld[param]

    def get_mountpoints(device):
        mountpoints_found = {}
        lsblk_mountpoints = subprocess.check_output(
            ['lsblk', '-l', '-n', '-o', 'TYPE,MOUNTPOINT,KNAME,PKNAME'])
        for line in lsblk_mountpoints.splitlines():
            line_as_list = ' '.join(line.split()).split(' ')
            # Only parse devices that are actually mounted
            if len(line_as_list) == 4:
               part_type = line_as_list[0]
               mountpoint = line_as_list[1]
               kname = line_as_list[2]
               pkname = line_as_list[3]
               if 'lvm' in part_type:
                   if device in pkname:
                       if pkname in mountpoints_found:
                           mountpoints_found[pkname] += ',\n       {}:{}'.format(
                               kname, mountpoint)
                       else:
                           mountpoints_found[pkname] = '{}:{}'.format(
                               kname, mountpoint)
               else:
                   if device in kname:
                       mountpoints_found[kname] = mountpoint
        return mountpoints_found

    def search_nested_structure(structure, searchstring):
        for k in structure:
            for v in structure:
                if searchstring in v:
                    return k
        return None


    # gather summary table data
    for line in disks_by_path.splitlines():
       logicaldevice_id = ''
       ld_raid_level = ''
       ld_state = ''
       ld_bad_block_exist = ''
       ld_status = ''
       device_name = ''
       device_mountspoints = ''
       member_disks = []
       member_params = ''
       member_status = ''
       if "scsi" in line:
           # get the logical device id which is part of the /dev/disk/by-path
           # string
           match = re.search('^.*pci-[0-9]{4}:[0-9]{2}:[0-9]{2}\.[0-9]-scsi-' \
                             '[0-9]:[0-9]:([0-9][0-9]*):[0-9]', line)
           if match:
               logicaldevice_id = match.group(1)
           # get the corresponding Linux device name
           match = re.search('(sd[a-zA-Z])', line)
           if match:
               device_name = match.group(1)
           # deal wiht non-LSI devices
           if not logicaldevice_id and device_name:
               for part, mounts in get_mountpoints(device_name).iteritems():
                   device_mountspoints += '{} @ {}\n'.format(part, mounts)
               if not search_nested_structure(table_data, device_name):
                   table_data.append([device_name, device_mountspoints,
                                     'non-LSI', 'N/A', 'N/A', 'N/A'])
               continue
           # gather data about LD
           ld_raid_level = get_logicaldrive_param(logicaldevice_id,
                                                  'raid_level')
           ld_state = get_logicaldrive_param(logicaldevice_id, 'state')
           ld_bad_block_exist = get_logicaldrive_param(logicaldevice_id,
                                                       'bad_blocks_exist')
           if ld_state.strip() != 'optimal' or ld_bad_block_exist:
               ld_status = 'LD state: {}\nBad blocks: {}'.format(
                   ld_state, ld_bad_block_exist)
           else:
               ld_status = 'OK'
           # avoid duplicates (partitions, respecively. E.g. sda1, sda2, etc.
           # are essentially all sda)
           if not search_nested_structure(table_data, device_name):
               for slot in ld_to_pd[logicaldevice_id]:
                   member_disks.append(get_physicaldrive_by_param(
                       physicaldrives,
                       'slot_number',
                       slot))
               for disk in member_disks:
                   member_params += '[{}:{}] {} ({})\n'.format(
                                    disk['enclosure_id'],
                                    disk['slot_number'],
                    hurry.filesize.size(disk['raw_size']),
                                        ' '.join(
                                        disk['inquiry_data'].upper().split()))
                   if (disk['firmware_state'].strip() != 'online, spun up' or
                       disk['media_error_count'] or
                       disk['drive_has_flagged_a_smart_alert']):
                       member_status += 'FW state: {}\nMedia Errors: {}\n' \
                                        'SMART alert: {}'.format(
                                        disk['firmware_state'],
                                        disk['media_error_count'],
                                        disk['drive_has_flagged_a_smart_alert'])
                   else:
                       member_status += 'OK\n'

               for part, mounts in get_mountpoints(device_name).iteritems():
                   device_mountspoints += '{} @ {}\n'.format(part, mounts)

               table_data.append([device_name,
                                  device_mountspoints,
                                  '{} (R{})'.format(logicaldevice_id,
                                                       ld_raid_level),
                                  ld_status,
                                  member_params, member_status])

    print '\n\n\n=======\nWARNING\n=======\nfc-megacli expects exactly 1 LSI ' \
          'controller and 1 enclosure!\nIn case you have multiple ' \
          'controllers and/or enclosures installed,\nDO NOT TRUST fc-megacli ' \
          'output!\n\n\n'
    table = terminaltables.SingleTable(table_data)
    print table.table
