"""The machine model and the CPU name, from fake sysfs / procfs trees."""
from utils.system.machine import cpu_name, machine_model


def tree(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def test_an_sbc_names_itself_in_the_device_tree(tmp_path):
    model = tmp_path / 'model'
    model.write_bytes(b'Raspberry Pi 4 Model B Rev 1.4\x00')
    assert machine_model(tmp_path / 'no-dmi', model) == 'Raspberry Pi 4 Model B Rev 1.4'


def test_a_branded_pc_is_vendor_and_product(tmp_path):
    dmi = tree(tmp_path, sys_vendor='Dell Inc.\n', product_name='OptiPlex 7010\n', board_vendor='Dell Inc.\n', board_name='0YXT71\n')
    assert machine_model(dmi, tmp_path / 'none') == 'Dell Inc. OptiPlex 7010'


def test_the_vendor_is_not_said_twice(tmp_path):
    dmi = tree(tmp_path, sys_vendor='LENOVO\n', product_name='Lenovo ThinkCentre M73\n')
    assert machine_model(dmi, tmp_path / 'none') == 'Lenovo ThinkCentre M73'


def test_a_diy_pc_is_its_motherboard(tmp_path):
    dmi = tree(tmp_path, sys_vendor='To Be Filled By O.E.M.\n', product_name='To Be Filled By O.E.M.\n',
               board_vendor='ASUSTeK COMPUTER INC.\n', board_name='ROG STRIX X299-E GAMING II\n')
    assert machine_model(dmi, tmp_path / 'none') == 'ASUSTeK COMPUTER INC. ROG STRIX X299-E GAMING II'


def test_a_machine_that_says_nothing_has_no_model(tmp_path):
    dmi = tree(tmp_path, sys_vendor='Default string\n', product_name='System Product Name\n', board_name='Default string\n')
    assert machine_model(dmi, tmp_path / 'none') is None
    assert machine_model(tmp_path / 'missing', tmp_path / 'none') is None


def test_cpu_name_one_socket(tmp_path):
    cpuinfo = tree(tmp_path, cpuinfo='processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz\n'
                                     'processor\t: 1\nmodel name\t: Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz\n') / 'cpuinfo'
    assert cpu_name(cpuinfo) == 'Intel Core i7-7800X CPU @ 3.50GHz'


def test_cpu_name_several_sockets(tmp_path):
    cpuinfo = tree(tmp_path, cpuinfo='model name\t: Xeon E5-2680\nphysical id\t: 0\nmodel name\t: Xeon E5-2680\nphysical id\t: 1\n') / 'cpuinfo'
    assert cpu_name(cpuinfo) == '2 x Xeon E5-2680'


def test_cpu_name_when_the_kernel_does_not_say(tmp_path):
    cpuinfo = tree(tmp_path, cpuinfo='processor\t: 0\nBogoMIPS\t: 108.00\n') / 'cpuinfo'
    assert cpu_name(cpuinfo) is None
    assert cpu_name(tmp_path / 'missing') is None
