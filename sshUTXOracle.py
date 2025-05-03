import paramiko
import sys
import time
from datetime import datetime, timezone, timedelta
import json
from math import log10
import argparse
import ipaddress  # For IP address validation

# Global SSH client
ssh = None

# Global bitcoin_cli_options (uncomment and configure if needed)
# bitcoin_cli_options = ["-rpcuser=user", "-rpcpassword=pass"]
# not needed when using ssh into start9 node.
bitcoin_cli_options = []

def validate_ip(ip):
    """Validate that the provided IP address is a valid IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False

def initialize_ssh(ip_address):
    """Initialize the global SSH connection with the specified IP address."""
    global ssh
    if not validate_ip(ip_address):
        print(f"Error: Invalid IP address '{ip_address}'. Please provide a valid IPv4 address.")
        sys.exit(1)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ip_address, username='start9')
        print(f"SSH connection successful to {ip_address}")
    except paramiko.AuthenticationException:
        print("Error: SSH authentication failed (check username/password)")
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"Error: SSH connection failed - {str(e)}")
        sys.exit(1)

def close_ssh():
    """Close the global SSH connection if open."""
    global ssh
    if ssh is not None:
        ssh.close()
        print("SSH connection closed")
        ssh = None

def Ask_Node(command):
    """Execute a bitcoin-cli command via SSH using the global SSH connection."""
    global ssh
    if ssh is None:
        raise Exception("SSH connection not initialized")

    try:
        # Construct the bitcoin-cli command
        cli_command = ["bitcoin-cli"]

        # Add any configuration options from bitcoin_cli_options
        #for o in bitcoin_cli_options:
        #    cli_command.append(str(o))

        # Add the user-provided command arguments, decoding bytes to hex if needed
        for arg in command:
            if isinstance(arg, bytes):
                cli_command.append(arg.decode('utf-8'))
            else:
                cli_command.append(str(arg))

        # Build the full podman command
        escaped_cli_command = [f'"{arg}"' if ' ' in arg else arg for arg in cli_command]
        podman_command = f"sudo podman exec bitcoind.embassy {' '.join(escaped_cli_command)}"

        # Execute the command via SSH
        stdin, stdout, stderr = ssh.exec_command(podman_command)

        # Read output and errors
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        # Explicitly close the channels
        stdin.close()
        stdout.close()
        stderr.close()

        # Check for errors
        if stdout.channel.recv_exit_status() != 0 or error:
            raise Exception(f"bitcoin-cli error: {error or 'Command failed without specific error'}")

        # Return the answer as bytes
        return output.encode()

    except paramiko.SSHException as e:
        print(f"Error executing command: SSH error - {str(e)}")
        raise
    except Exception as e:
        print("Error connecting to your node. Troubleshooting steps:")
        print("\t1) Make sure the bitcoind.embassy container is running on the specified IP")
        print("\t2) Verify 'sudo podman exec bitcoind.embassy bitcoin-cli getblockcount' works via SSH")
        print("\t3) Ensure bitcoin.conf has server=1 and correct RPC credentials")
        print("\t4) Check bitcoin_cli_options for correct RPC user/password")
        print(f"\nThe command was: {podman_command if 'podman_command' in locals() else 'not constructed'}")
        print(f"\nThe error was:\n{str(e)}")
        raise

# Parse command-line arguments
parser = argparse.ArgumentParser(description="UTXOracle: Estimate Bitcoin price from on-chain data")
parser.add_argument('--ip', type=str, default='192.168.1.99',
                    help="IP address of the Bitcoin node (default: 192.168.1.99)")
args = parser.parse_args()

# Initialize SSH connection with the provided or default IP
initialize_ssh(args.ip)

try:
    # Main loop to allow multiple price estimates until 'q'
    while True:
        ###############################################################################
        # Part 2) Get the latest block from the node
        ###############################################################################

        block_count_b = Ask_Node(['getblockcount'])
        block_count = int(block_count_b)

        block_hash_b = Ask_Node(['getblockhash', str(block_count)])
        block_header_b = Ask_Node(['getblockheader', block_hash_b, 'true'])
        block_header = json.loads(block_header_b)

        latest_time_in_seconds = block_header['time']
        time_datetime = datetime.fromtimestamp(latest_time_in_seconds, tz=timezone.utc)

        latest_year = int(time_datetime.strftime("%Y"))
        latest_month = int(time_datetime.strftime("%m"))
        latest_day = int(time_datetime.strftime("%d"))
        latest_utc_midnight = datetime(latest_year, latest_month, latest_day, 0, 0, 0, tzinfo=timezone.utc)

        seconds_in_a_day = 60 * 60 * 24
        yesterday_seconds = latest_time_in_seconds - seconds_in_a_day
        latest_price_day = datetime.fromtimestamp(yesterday_seconds, tz=timezone.utc)
        latest_price_date = latest_price_day.strftime("%Y-%m-%d")

        print("UTXOracle version 8")
        print("\nConnected to local node at block #:\t" + str(block_count))
        print("Latest available price date:\t\t" + latest_price_date + " (pruned node ok)")
        print("Earliest available price date:\t\t2023-12-15 (requires full node)")

        ###############################################################################
        # Part 3) Ask the user for the desired date to estimate the price
        ###############################################################################

        date_entered = input("\nEnter date in YYYY-MM-DD format\nor Enter 'q' to quit " +
                             "\nor press ENTER for the most recent price: ")

        if date_entered == 'q':
            print("Exiting script...")
            break  # Exit the loop to close SSH and exit

        elif date_entered == "":
            datetime_entered = latest_utc_midnight + timedelta(days=-1)
        else:
            try:
                year = int(date_entered.split('-')[0])
                month = int(date_entered.split('-')[1])
                day = int(date_entered.split('-')[2])
                datetime_entered = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
                if datetime_entered.timestamp() > latest_utc_midnight.timestamp():
                    print("\nThe date entered is not before the current date, please try again")
                    continue
                dec_15_2023 = datetime(2023, 12, 15, 0, 0, 0, tzinfo=timezone.utc)
                if datetime_entered.timestamp() < dec_15_2023.timestamp():
                    print("\nThe date entered is before 2023-12-15, please try again")
                    continue
            except:
                print("\nError interpreting date. Please try again. Make sure format is YYYY-MM-DD")
                continue

        price_day_seconds = int(datetime_entered.timestamp())
        price_day_date_utc = datetime_entered.strftime("%B %d, %Y")
        print("\n\n########   Starting Price Estimate   ########")

        ###############################################################################
        # Part 4) Hunt through blocks to find the first block on the target day
        ###############################################################################

        seconds_since_price_day = latest_time_in_seconds - price_day_seconds
        blocks_ago_estimate = round(144 * float(seconds_since_price_day) / float(seconds_in_a_day))
        price_day_block_estimate = block_count - blocks_ago_estimate

        block_hash_b = Ask_Node(['getblockhash', str(price_day_block_estimate)])
        block_header_b = Ask_Node(['getblockheader', block_hash_b, 'true'])
        block_header = json.loads(block_header_b)
        time_in_seconds = block_header['time']

        seconds_difference = time_in_seconds - price_day_seconds
        block_jump_estimate = round(144 * float(seconds_difference) / float(seconds_in_a_day))

        last_estimate = 0
        last_last_estimate = 0
        while block_jump_estimate > 6 and block_jump_estimate != last_last_estimate:
            last_last_estimate = last_estimate
            last_estimate = block_jump_estimate
            price_day_block_estimate = price_day_block_estimate - block_jump_estimate
            block_hash_b = Ask_Node(['getblockhash', str(price_day_block_estimate)])
            block_header_b = Ask_Node(['getblockheader', block_hash_b, 'true'])
            block_header = json.loads(block_header_b)
            time_in_seconds = block_header['time']
            seconds_difference = time_in_seconds - price_day_seconds
            block_jump_estimate = round(144 * float(seconds_difference) / float(seconds_in_a_day))

        if time_in_seconds > price_day_seconds:
            while time_in_seconds > price_day_seconds:
                price_day_block_estimate = price_day_block_estimate - 1
                block_hash_b = Ask_Node(['getblockhash', str(price_day_block_estimate)])
                block_header_b = Ask_Node(['getblockheader', block_hash_b, 'true'])
                block_header = json.loads(block_header_b)
                time_in_seconds = block_header['time']
            price_day_block_estimate = price_day_block_estimate + 1
        elif time_in_seconds < price_day_seconds:
            while time_in_seconds < price_day_seconds:
                price_day_block_estimate = price_day_block_estimate + 1
                block_hash_b = Ask_Node(['getblockhash', str(price_day_block_estimate)])
                block_header_b = Ask_Node(['getblockheader', block_hash_b, 'true'])
                block_header = json.loads(block_header_b)
                time_in_seconds = block_header['time']

        price_day_block = price_day_block_estimate

        ###############################################################################
        # Part 5) Build the container to hold the output amounts bell curve
        ###############################################################################

        first_bin_value = -6
        last_bin_value = 6
        range_bin_values = last_bin_value - first_bin_value

        output_bell_curve_bins = [0.0]
        for exponent in range(-6, 6):
            for b in range(0, 200):
                bin_value = 10 ** (exponent + b / 200)
                output_bell_curve_bins.append(bin_value)

        number_of_bins = len(output_bell_curve_bins)
        output_bell_curve_bin_counts = [0.0] * number_of_bins

        ###############################################################################
        # Part 6) Get all output amounts from all blocks on target day
        ###############################################################################

        print("\nReading all blocks on " + price_day_date_utc + "...")
        print("This will take a few minutes (~144 blocks)...")
        print("\nBlock Height\t Block Time(utc)\t\tCompletion %")

        block_height = price_day_block
        block_hash_b = Ask_Node(['getblockhash', str(block_height)])
        block_b = Ask_Node(['getblock', block_hash_b, '2'])
        block = json.loads(block_b)

        time_in_seconds = int(block['time'])
        time_datetime = datetime.fromtimestamp(time_in_seconds, tz=timezone.utc)
        time_utc = time_datetime.strftime(" %Y-%m-%d %H:%M:%S")
        hour_of_day = int(time_datetime.strftime("%H"))
        minute_of_hour = float(time_datetime.strftime("%M"))
        day_of_month = int(time_datetime.strftime("%d"))
        target_day_of_month = day_of_month

        todays_txids = set()

        while target_day_of_month == day_of_month:
            progress_estimate = 100.0 * (hour_of_day + minute_of_hour / 60) / 24.0
            print(str(block_height) + "\t\t" + time_utc + "\t\t" + f"{progress_estimate:.2f}" + "%")

            for tx in block['tx']:
                todays_txids.add(tx['txid'][-8:])
                inputs = tx['vin']
                outputs = tx['vout']

                if "coinbase" in inputs[0]:
                    continue
                if len(inputs) > 5:
                    continue
                if len(outputs) < 2:
                    continue
                if len(outputs) > 2:
                    continue

                has_op_return = False
                for output in outputs:
                    script_pub_key = output.get("scriptPubKey", {})
                    if script_pub_key.get("type") == "nulldata" or "OP_RETURN" in script_pub_key.get("asm", ""):
                        has_op_return = True
                        break
                if has_op_return:
                    continue

                has_sameday_input = False
                has_big_witness = False
                for inpt in inputs:
                    if 'txid' in inpt and inpt['txid'][-8:] in todays_txids:
                        has_sameday_input = True
                        break
                    if "txinwitness" in inpt:
                        for witness in inpt["txinwitness"]:
                            if len(witness) > 500:
                                has_big_witness = True
                                break
                    if has_sameday_input or has_big_witness:
                        break

                if has_sameday_input or has_big_witness:
                    continue

                for output in outputs:
                    amount = float(output['value'])
                    if 1e-5 < amount < 1e5:
                        amount_log = log10(amount)
                        percent_in_range = (amount_log - first_bin_value) / range_bin_values
                        bin_number_est = int(percent_in_range * number_of_bins)
                        while output_bell_curve_bins[bin_number_est] <= amount:
                            bin_number_est += 1
                        bin_number = bin_number_est - 1
                        output_bell_curve_bin_counts[bin_number] += 1.0

            block_height = block_height + 1
            block_hash_b = Ask_Node(['getblockhash', str(block_height)])
            block_b = Ask_Node(['getblock', block_hash_b, '2'])
            block = json.loads(block_b)

            time_in_seconds = int(block['time'])
            time_datetime = datetime.fromtimestamp(time_in_seconds, tz=timezone.utc)
            time_utc = time_datetime.strftime(" %Y-%m-%d %H:%M:%S")
            day_of_month = int(time_datetime.strftime("%d"))
            minute_of_hour = float(time_datetime.strftime("%M"))
            hour_of_day = int(time_datetime.strftime("%H"))

        ###############################################################################
        # Part 7) Remove non-usd related outputs from the bell curve
        ###############################################################################

        for n in range(0, 201):
            output_bell_curve_bin_counts[n] = 0
        for n in range(1601, len(output_bell_curve_bin_counts)):
            output_bell_curve_bin_counts[n] = 0

        round_btc_bins = [
            201, 401, 461, 496, 540, 601, 661, 696, 740, 801, 861, 896, 940, 1001, 1061, 1096, 1140, 1201
        ]

        for r in round_btc_bins:
            amount_above = output_bell_curve_bin_counts[r + 1]
            amount_below = output_bell_curve_bin_counts[r - 1]
            output_bell_curve_bin_counts[r] = 0.5 * (amount_above + amount_below)

        curve_sum = 0.0
        for n in range(201, 1601):
            curve_sum += output_bell_curve_bin_counts[n]

        for n in range(201, 1601):
            output_bell_curve_bin_counts[n] /= curve_sum
            if output_bell_curve_bin_counts[n] > 0.008:
                output_bell_curve_bin_counts[n] = 0.008

        ###############################################################################
        # Part 8) Construct the USD price finder stencils
        ###############################################################################

        num_elements = 803
        mean = 411
        std_dev = 201

        smooth_stencil = []
        for x in range(num_elements):
            exp_part = -((x - mean) ** 2) / (2 * (std_dev ** 2))
            smooth_stencil.append((0.00150 * 2.718281828459045 ** exp_part) + (0.0000005 * x))

        spike_stencil = [0.0] * 803
        spike_stencil[40] = 0.001300198324984352
        spike_stencil[141] = 0.001676746949820743
        spike_stencil[201] = 0.003468805546942046
        spike_stencil[202] = 0.001991977522512513
        spike_stencil[236] = 0.001905066647961839
        spike_stencil[261] = 0.003341772718156079
        spike_stencil[262] = 0.002588902624584287
        spike_stencil[296] = 0.002577893841190244
        spike_stencil[297] = 0.002733728814200412
        spike_stencil[340] = 0.003076117748975647
        spike_stencil[341] = 0.005613067550103145
        spike_stencil[342] = 0.003088253178535568
        spike_stencil[400] = 0.002918457489366139
        spike_stencil[401] = 0.006174500465286022
        spike_stencil[402] = 0.004417068070043504
        spike_stencil[403] = 0.002628663628020371
        spike_stencil[436] = 0.002858828161543839
        spike_stencil[461] = 0.004097463611984264
        spike_stencil[462] = 0.003345917406120509
        spike_stencil[496] = 0.002521467726855856
        spike_stencil[497] = 0.002784125730361008
        spike_stencil[541] = 0.003792850444811335
        spike_stencil[601] = 0.003688240815848247
        spike_stencil[602] = 0.002392400117402263
        spike_stencil[636] = 0.001280993059008106
        spike_stencil[661] = 0.001654665137536031
        spike_stencil[662] = 0.001395501347054946
        spike_stencil[741] = 0.001154279140906312
        spike_stencil[801] = 0.000832244504868709

        ###############################################################################
        # Part 9) Estimate the price using the best fit stencil slide
        ###############################################################################

        best_slide = 0
        best_slide_score = 0
        total_score = 0
        smooth_weight = 0.65
        spike_weight = 1

        center_p001 = 601
        left_p001 = center_p001 - int((len(spike_stencil) + 1) / 2)
        right_p001 = center_p001 + int((len(spike_stencil) + 1) / 2)

        min_slide = -141
        max_slide = 201

        for slide in range(min_slide, max_slide):
            shifted_curve = output_bell_curve_bin_counts[left_p001 + slide:right_p001 + slide]
            slide_score_smooth = 0.0
            for n in range(0, len(smooth_stencil)):
                slide_score_smooth += shifted_curve[n] * smooth_stencil[n]
            slide_score = 0.0
            for n in range(0, len(spike_stencil)):
                slide_score += shifted_curve[n] * spike_stencil[n]
            if slide < 150:
                slide_score = slide_score + slide_score_smooth * 0.65
            if slide_score > best_slide_score:
                best_slide_score = slide_score
                best_slide = slide
            total_score += slide_score

        usd100_in_btc_best = output_bell_curve_bins[center_p001 + best_slide]
        btc_in_usd_best = 100 / (usd100_in_btc_best)

        neighbor_up = output_bell_curve_bin_counts[left_p001 + best_slide + 1:right_p001 + best_slide + 1]
        neighbor_up_score = 0.0
        for n in range(0, len(spike_stencil)):
            neighbor_up_score += neighbor_up[n] * spike_stencil[n]

        neighbor_down = output_bell_curve_bin_counts[left_p001 + best_slide - 1:right_p001 + best_slide - 1]
        neighbor_down_score = 0.0
        for n in range(0, len(spike_stencil)):
            neighbor_down_score += neighbor_down[n] * spike_stencil[n]

        best_neighbor = +1
        neighbor_score = neighbor_up_score
        if neighbor_down_score > neighbor_up_score:
            best_neighbor = -1
            neighbor_score = neighbor_down_score

        usd100_in_btc_2nd = output_bell_curve_bins[center_p001 + best_slide + best_neighbor]
        btc_in_usd_2nd = 100 / (usd100_in_btc_2nd)

        avg_score = total_score / len(range(min_slide, max_slide))
        a1 = best_slide_score - avg_score
        a2 = abs(neighbor_score - avg_score)
        w1 = a1 / (a1 + a2)
        w2 = a2 / (a1 + a2)
        price_estimate = int(w1 * btc_in_usd_best + w2 * btc_in_usd_2nd)

        print("\nThe " + price_day_date_utc + " btc price estimate is: $" + f'{price_estimate:,}')

except Exception as e:
    print(f"\nAn unexpected error occurred: {str(e)}")
finally:
    close_ssh()
    sys.exit(0)
